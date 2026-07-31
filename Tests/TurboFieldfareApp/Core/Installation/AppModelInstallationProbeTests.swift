import Darwin
import Foundation
import Testing
import TurboFieldfare
import TurboFieldfareRepackCore
@testable import TurboFieldfareAppCore

@Suite struct AppModelInstallationProbeTests {
    @Test func missingDirectoryIsMissing() {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("turbofieldfare-missing-\(UUID().uuidString).gturbo")
        #expect(AppModelInstallationProbe.status(at: url) == .missing)
    }

    @Test func manifestWithoutFinalMetadataIsPartial() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("turbofieldfare-partial-\(UUID().uuidString).gturbo")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("{}".utf8).write(to: url.appendingPathComponent("manifest.json"))
        guard case .partial = AppModelInstallationProbe.status(at: url) else {
            Issue.record("expected partial status")
            return
        }
    }

    @Test func validBoundedMetadataIsComplete() throws {
        let url = try makeCompleteModelInstall("probe")
        defer { try? FileManager.default.removeItem(at: url) }
        #expect(AppModelInstallationProbe.status(at: url) == .complete)
    }

    @Test func receiptBoundToDifferentPathIsPartial() throws {
        let url = try makeCompleteModelInstall("wrong-path")
        defer { try? FileManager.default.removeItem(at: url) }
        let receiptURL = url.appendingPathComponent("verified-install.json")
        var receipt = try JSONSerialization.jsonObject(with: Data(contentsOf: receiptURL)) as! [String: Any]
        receipt["modelDirectoryPath"] = "/different/model.gturbo"
        try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]).write(to: receiptURL)
        guard case .partial = AppModelInstallationProbe.status(at: url) else {
            Issue.record("expected partial status")
            return
        }
    }

    @Test func missingRequiredTokenizerSidecarIsPartial() throws {
        let url = try makeCompleteModelInstall("missing-tokenizer")
        defer { try? FileManager.default.removeItem(at: url) }
        try FileManager.default.removeItem(
            at: url.appendingPathComponent("tokenizer/tokenizer.json"))

        guard case .partial(let reason) = AppModelInstallationProbe.status(
            at: url) else {
            Issue.record("expected partial status")
            return
        }
        #expect(reason.contains("tokenizer/tokenizer.json is missing"))
    }

    @Test func sameSizeTokenizerCorruptionIsPartial() throws {
        let url = try makeCompleteModelInstall("corrupt-tokenizer")
        defer { try? FileManager.default.removeItem(at: url) }
        try Data("[]".utf8).write(
            to: url.appendingPathComponent("tokenizer/tokenizer.json"))

        guard case .partial(let reason) = AppModelInstallationProbe.status(
            at: url) else {
            Issue.record("expected partial status")
            return
        }
        #expect(reason.contains("SHA-256 does not match"))
    }

    @Test func manifestModelIDMustMatchDescriptor() throws {
        let url = try makeCompleteModelInstall("wrong-model-id")
        defer { try? FileManager.default.removeItem(at: url) }
        let manifestURL = url.appendingPathComponent("manifest.json")
        var manifest = try JSONSerialization.jsonObject(
            with: Data(contentsOf: manifestURL)) as! [String: Any]
        manifest["modelID"] = "different/repository"
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.sortedKeys]).write(to: manifestURL)

        guard case .partial(let reason) = AppModelInstallationProbe.status(
            at: url) else {
            Issue.record("expected partial status")
            return
        }
        #expect(reason.contains("installed checkpoint does not match"))
    }

    @Test func receiptSourceIdentityMustMatchDescriptor() throws {
        let url = try makeCompleteModelInstall("wrong-receipt-source")
        defer { try? FileManager.default.removeItem(at: url) }
        let receiptURL = url.appendingPathComponent("verified-install.json")
        var receipt = try JSONSerialization.jsonObject(
            with: Data(contentsOf: receiptURL)) as! [String: Any]
        receipt["sourceRepoID"] = "different/repository"
        try JSONSerialization.data(
            withJSONObject: receipt,
            options: [.sortedKeys]).write(to: receiptURL)

        guard case .partial(let reason) = AppModelInstallationProbe.status(
            at: url) else {
            Issue.record("expected partial status")
            return
        }
        #expect(reason.contains("verified install source does not match"))
    }

    @Test func legacyVerifierReceiptRemainsAccepted() throws {
        let descriptor = AppModelInstallDescriptor.superGemma
        let url = try makeCompleteModelInstall(
            "legacy-verifier-receipt",
            descriptor: descriptor)
        defer { try? FileManager.default.removeItem(at: url) }
        let receiptURL = url.appendingPathComponent("verified-install.json")
        var receipt = try JSONSerialization.jsonObject(
            with: Data(contentsOf: receiptURL)) as! [String: Any]
        receipt.removeValue(forKey: "sourceRepoID")
        receipt["sourceRevision"] =
            "sha256:" + descriptor.sourceIndexSHA256
        try JSONSerialization.data(
            withJSONObject: receipt,
            options: [.sortedKeys]).write(to: receiptURL)

        #expect(AppModelInstallationProbe.status(
            at: url,
            descriptor: descriptor) == .complete)
    }

    @Test func differentCheckpointIsPartial() throws {
        let url = try makeCompleteModelInstall("wrong-checkpoint")
        defer { try? FileManager.default.removeItem(at: url) }
        let descriptor = AppModelInstallDescriptor(
            displayName: "different",
            repoID: "example/different",
            revision: "revision",
            sourceIndexSHA256: String(repeating: "f", count: 64),
            approximateDownloadBytes: 1,
            installedBytes: 1,
            rangeStagingBytes: 1,
            reserveBytes: 1)
        guard case .partial = AppModelInstallationProbe.status(at: url, descriptor: descriptor) else {
            Issue.record("expected checkpoint mismatch to be partial")
            return
        }
    }

    @Test func superGemmaInstallRequiresItsOwnDescriptor() throws {
        let descriptor = AppModelInstallDescriptor.superGemma
        let url = try makeCompleteModelInstall(
            "supergemma-probe",
            descriptor: descriptor)
        defer { try? FileManager.default.removeItem(at: url) }

        #expect(AppModelInstallationProbe.status(
            at: url,
            descriptor: descriptor) == .complete)
        guard case .partial = AppModelInstallationProbe.status(
            at: url,
            descriptor: .stock) else {
            Issue.record("expected Stock descriptor to reject SuperGemma")
            return
        }
    }

    @Test func verifyInstallKeepsKnownSuperGemmaComplete() throws {
        let descriptor = AppModelInstallDescriptor.superGemma
        let url = try makeCompleteModelInstall(
            "supergemma-verify-round-trip",
            descriptor: descriptor)
        defer { try? FileManager.default.removeItem(at: url) }

        let pageSize = UInt64(getpagesize())
        let modelWeightsURL = url.appendingPathComponent("model_weights.bin")
        let layerURL = url.appendingPathComponent(
            "packed_experts/layer_00.bin")
        try Data().write(to: modelWeightsURL)
        try Data(repeating: 0x5A, count: Int(pageSize)).write(to: layerURL)
        let layout: [String: Any] = [
            "expertStride": pageSize,
            "numLayers": 1,
            "expertsPerLayer": 1,
            "layers": [[
                "layer": 0,
                "file": "layer_00.bin",
                "experts": [[
                    "expert": 0,
                    "offset": 0,
                    "size": pageSize,
                ]],
            ]],
        ]
        let layoutData = try JSONSerialization.data(
            withJSONObject: layout,
            options: [.sortedKeys])
        let layoutURL = url.appendingPathComponent(
            "packed_experts/layout.json")
        try layoutData.write(to: layoutURL)
        let tokenizerData = Data("{}".utf8)

        let manifestURL = url.appendingPathComponent("manifest.json")
        var manifest = try JSONSerialization.jsonObject(
            with: Data(contentsOf: manifestURL)) as! [String: Any]
        manifest["numLayers"] = 1
        manifest["expertsPerLayer"] = 1
        manifest["expertStride"] = pageSize
        manifest["files"] = [
            "model_weights.bin": [
                "size": 0,
                "sha256": Sha256Verifier.hashData(Data()),
            ],
            "packed_experts/layout.json": [
                "size": UInt64(layoutData.count),
                "sha256": try Sha256Verifier.hashFile(
                    at: layoutURL,
                    chunkBytes: 65_536),
            ],
            "packed_experts/layer_00.bin": [
                "size": pageSize,
                "sha256": try Sha256Verifier.hashFile(
                    at: layerURL,
                    chunkBytes: 65_536),
            ],
            "tokenizer/tokenizer.json": [
                "size": UInt64(tokenizerData.count),
                "sha256": Sha256Verifier.hashData(tokenizerData),
            ],
            "tokenizer/tokenizer_config.json": [
                "size": UInt64(tokenizerData.count),
                "sha256": Sha256Verifier.hashData(tokenizerData),
            ],
        ]
        try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.sortedKeys]).write(to: manifestURL)

        _ = try VerifiedInstallTool.run(
            options: VerifyInstallOptions(inputGTurbo: url.path))

        #expect(AppModelInstallationProbe.status(
            at: url,
            descriptor: descriptor) == .complete)
    }
}
