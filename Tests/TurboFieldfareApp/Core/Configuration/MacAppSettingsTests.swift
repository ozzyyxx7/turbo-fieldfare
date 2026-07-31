import Foundation
import Testing
@testable import TurboFieldfareAppCore

@Suite struct MacAppSettingsTests {
    @Test func settingsFileLivesBesideModelDirectory() {
        let model = URL(fileURLWithPath: "/tmp/TurboFieldfare/gemma4.gturbo",
                        isDirectory: true)
        #expect(MacAppSettingsFileStore.fileURL(forModelDirectory: model).path
            == "/tmp/TurboFieldfare/mac-app-settings.json")
    }

    @Test func missingFileCreatesReadableDefaults() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let model = root.appendingPathComponent("gemma4.gturbo", isDirectory: true)

        let settings = MacAppSettingsFileStore.loadOrCreate(forModelDirectory: model)
        let fileURL = MacAppSettingsFileStore.fileURL(forModelDirectory: model)

        #expect(settings == MacAppSettings())
        #expect(FileManager.default.fileExists(atPath: fileURL.path))
        let decoded = try JSONDecoder().decode(
            MacAppSettings.self,
            from: Data(contentsOf: fileURL))
        #expect(decoded == MacAppSettings())
    }

    @Test func malformedFileIsReplacedWithDefaults() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let model = root.appendingPathComponent("gemma4.gturbo", isDirectory: true)
        let fileURL = MacAppSettingsFileStore.fileURL(forModelDirectory: model)
        try Data("not json".utf8).write(to: fileURL)

        let settings = MacAppSettingsFileStore.loadOrCreate(forModelDirectory: model)

        #expect(settings == MacAppSettings())
        let decoded = try JSONDecoder().decode(
            MacAppSettings.self,
            from: Data(contentsOf: fileURL))
        #expect(decoded == MacAppSettings())
    }

    @Test func invalidValuesAreReplacedWithDefaults() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let model = root.appendingPathComponent("gemma4.gturbo", isDirectory: true)
        let invalid = MacAppSettings(contextTokens: 123)
        let fileURL = MacAppSettingsFileStore.fileURL(forModelDirectory: model)
        try JSONEncoder().encode(invalid).write(to: fileURL)

        let settings = MacAppSettingsFileStore.loadOrCreate(forModelDirectory: model)

        #expect(settings == MacAppSettings())
    }

    @Test func legacyVersionOneSettingsResolveToStock() throws {
        let legacy = """
        {
          "version": 1,
          "contextTokens": 4096,
          "expertCacheSlots": 16,
          "temperature": 0.2,
          "topKEnabled": true,
          "topK": 64,
          "topPEnabled": true,
          "topP": 0.95,
          "prefillEnabled": true
        }
        """
        let settings = try JSONDecoder().decode(
            MacAppSettings.self,
            from: Data(legacy.utf8))

        #expect(settings.modelSourceID == nil)
        #expect(settings.resolvedModelSourceID ==
            AppModelInstallDescriptor.stock.id)
        #expect(settings.isValid())
    }

    @Test func modelSourceRoundTripsAndUnknownFallsBackToStock() throws {
        let selected = MacAppSettings(
            modelSourceID: AppModelInstallDescriptor.superGemma.id)
        let roundTrip = try JSONDecoder().decode(
            MacAppSettings.self,
            from: JSONEncoder().encode(selected))

        #expect(roundTrip.resolvedModelSourceID ==
            AppModelInstallDescriptor.superGemma.id)
        var unknown = roundTrip
        unknown.modelSourceID = "removed-profile"
        #expect(unknown.resolvedModelSourceID ==
            AppModelInstallDescriptor.stock.id)
        #expect(unknown.isValid())
    }

    @MainActor
    @Test func appModelLoadsAndSavesPersistedSettings() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let modelDirectory = root.appendingPathComponent("gemma4.gturbo", isDirectory: true)
        try FileManager.default.createDirectory(
            at: modelDirectory,
            withIntermediateDirectories: true)
        let initial = MacAppSettings(
            contextTokens: 8_192,
            expertCacheSlots: 24,
            temperature: 0.4,
            topKEnabled: false,
            topK: 32,
            topPEnabled: false,
            topP: 0.8,
            prefillEnabled: false)
        try MacAppSettingsFileStore.save(initial, forModelDirectory: modelDirectory)

        let model = AppModel(
            modelDirectory: modelDirectory,
            settingsPersistenceEnabled: true)
        #expect(model.maxContextTokens == 8_192)
        #expect(model.runtimeOptions.expertCacheSlots == 24)
        #expect(model.temperature == 0.4)
        #expect(!model.topKEnabled)
        #expect(model.topK == 32)
        #expect(!model.topPEnabled)
        #expect(model.topP == 0.8)
        #expect(!model.runtimeOptions.prefillEnabled)

        model.temperature = 0.6
        model.runtimeOptions.expertCacheSlots = 32
        model.runtimeOptions.prefillEnabled = true
        let beforeGenerate = MacAppSettingsFileStore.loadOrCreate(
            forModelDirectory: modelDirectory)
        #expect(beforeGenerate == initial)

        model.loadState = .ready(modelDirectory: modelDirectory, loadSeconds: 0)
        model.promptText = "Save these settings"
        model.run()
        let saved = MacAppSettingsFileStore.loadOrCreate(
            forModelDirectory: modelDirectory)
        #expect(saved.temperature == 0.6)
        #expect(saved.expertCacheSlots == 32)
        #expect(saved.prefillEnabled)
        model.cancel()
    }

    @MainActor
    @Test func appModelRestoresPersistedSuperGemmaSelection() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let stockDirectory = root.appendingPathComponent(
            AppModelInstallDescriptor.stock.installFileName,
            isDirectory: true)
        try MacAppSettingsFileStore.save(
            MacAppSettings(
                modelSourceID: AppModelInstallDescriptor.superGemma.id,
                contextTokens: 8_192,
                expertCacheSlots: 24),
            forModelDirectory: stockDirectory)

        let model = AppModel(
            client: MockInferenceClient(),
            installerFactory: {
                MockModelInstallerClient(descriptor: $0)
            },
            settingsPersistenceEnabled: true,
            modelLocationResolver: {
                root.appendingPathComponent(
                    $0.installFileName,
                    isDirectory: true)
            })

        #expect(model.installDescriptor == .superGemma)
        #expect(model.modelPathText.hasSuffix("/supergemma4.gturbo"))
        #expect(model.maxContextTokens == 8_192)
        #expect(model.runtimeOptions.expertCacheSlots == 24)
    }

    @MainActor
    @Test func changingVariantPreservesAndPersistsCurrentControls() throws {
        let root = try makeTemporaryRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let stockDirectory = root.appendingPathComponent(
            AppModelInstallDescriptor.stock.installFileName,
            isDirectory: true)
        let model = AppModel(
            modelDirectory: stockDirectory,
            client: MockInferenceClient(),
            installer: MockModelInstallerClient(descriptor: .stock),
            installerFactory: {
                MockModelInstallerClient(descriptor: $0)
            },
            settingsPersistenceEnabled: true,
            modelLocationResolver: {
                root.appendingPathComponent(
                    $0.installFileName,
                    isDirectory: true)
            })
        model.maxContextTokens = 16_384
        model.runtimeOptions.expertCacheSlots = 32
        model.runtimeOptions.prefillEnabled = false
        model.temperature = 0.6
        model.topK = 32
        model.topP = 0.8

        model.selectInstallDescriptor(id: AppModelInstallDescriptor.superGemma.id)

        #expect(model.installDescriptor == .superGemma)
        #expect(model.maxContextTokens == 16_384)
        #expect(model.runtimeOptions.expertCacheSlots == 32)
        #expect(!model.runtimeOptions.prefillEnabled)
        #expect(model.temperature == 0.6)
        #expect(model.topK == 32)
        #expect(model.topP == 0.8)
        let saved = MacAppSettingsFileStore.loadOrCreate(
            forModelDirectory: URL(fileURLWithPath: model.modelPathText))
        #expect(saved.modelSourceID == AppModelInstallDescriptor.superGemma.id)
        #expect(saved.contextTokens == 16_384)
        #expect(saved.expertCacheSlots == 32)
    }

    private func makeTemporaryRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("MacAppSettingsTests-\(UUID().uuidString)",
                                    isDirectory: true)
        try FileManager.default.createDirectory(
            at: root,
            withIntermediateDirectories: true)
        return root
    }
}
