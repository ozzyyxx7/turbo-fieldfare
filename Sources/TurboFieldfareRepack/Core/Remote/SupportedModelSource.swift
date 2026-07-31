import Foundation

public enum SupportedModelSource {
    public struct Profile: Identifiable, Hashable, Sendable {
        public let id: String
        public let displayName: String
        public let shortDisplayName: String
        public let repoID: String
        public let revision: String
        public let sourceIndexSHA256: String
        public let approximateDownloadBytes: UInt64
        public let installedBytes: UInt64
        public let reserveBytes: UInt64
        public let installFileName: String
        public let apiModelID: String

        public init(id: String,
                    displayName: String,
                    shortDisplayName: String,
                    repoID: String,
                    revision: String,
                    sourceIndexSHA256: String,
                    approximateDownloadBytes: UInt64,
                    installedBytes: UInt64,
                    reserveBytes: UInt64,
                    installFileName: String,
                    apiModelID: String) {
            self.id = id
            self.displayName = displayName
            self.shortDisplayName = shortDisplayName
            self.repoID = repoID
            self.revision = revision
            self.sourceIndexSHA256 = sourceIndexSHA256
            self.approximateDownloadBytes = approximateDownloadBytes
            self.installedBytes = installedBytes
            self.reserveBytes = reserveBytes
            self.installFileName = installFileName
            self.apiModelID = apiModelID
        }

        public func installOptions(outputDirectory: URL,
                                   overwrite: Bool,
                                   token: String?,
                                   resume: Bool = false)
            -> RemoteStreamingRepackOptions {
            RemoteStreamingRepackOptions(
                repoID: repoID,
                revision: revision,
                outputDir: outputDirectory.path,
                token: token,
                requireKnownSource: true,
                minFreeReserveBytes: reserveBytes,
                overwrite: overwrite,
                resume: resume)
        }
    }

    public static let stock = Profile(
        id: "stock",
        displayName: "Gemma 4 26B-A4B IT 4-bit",
        shortDisplayName: "Gemma 4 26B",
        repoID: "mlx-community/gemma-4-26b-a4b-it-4bit",
        revision: "0d77464eeb233a2da68ebf9d7dc4edaac7db956d",
        sourceIndexSHA256:
            "bf198c9f5ea6462addca1966e5dd669c407537a876e82cf06db9084c5c850b13",
        approximateDownloadBytes: 14_620_479_420,
        installedBytes: 14_291_921_884,
        reserveBytes: 1_073_741_824,
        installFileName: "gemma4.gturbo",
        apiModelID: "gemma-4-26b-a4b-it")

    public static let superGemma = Profile(
        id: "supergemma",
        displayName: "SuperGemma 4 26B-A4B Uncensored 4-bit v2",
        shortDisplayName: "SuperGemma 4 26B",
        repoID: "Jiunsong/supergemma4-26b-uncensored-mlx-4bit-v2",
        revision: "1ecb7582718b813fc4c7b5c3131b2b7053787f00",
        sourceIndexSHA256:
            "df3133d5e9e400092664cb2197413a32035189ab7c41f4b000f75a284abdc512",
        approximateDownloadBytes: 14_232_570_769,
        installedBytes: 14_291_921_884,
        reserveBytes: 1_073_741_824,
        installFileName: "supergemma4.gturbo",
        apiModelID: "supergemma-4-26b-a4b-uncensored")

    public static let all: [Profile] = [stock, superGemma]
    public static let defaultProfile = stock

    public static func profile(named value: String) -> Profile? {
        let normalized = value.lowercased()
        return all.first {
            $0.id == normalized
                || $0.repoID.lowercased() == normalized
                || ($0.id == superGemma.id
                    && ["super", "supergemma4"].contains(normalized))
        }
    }

    // Compatibility aliases keep existing callers on the stock profile.
    public static var displayName: String { defaultProfile.displayName }
    public static var repoID: String { defaultProfile.repoID }
    public static var revision: String { defaultProfile.revision }
    public static var sourceIndexSHA256: String { defaultProfile.sourceIndexSHA256 }
    public static var approximateDownloadBytes: UInt64 {
        defaultProfile.approximateDownloadBytes
    }
    public static var installedBytes: UInt64 { defaultProfile.installedBytes }
    public static var reserveBytes: UInt64 { defaultProfile.reserveBytes }

    public static func installOptions(outputDirectory: URL,
                                      overwrite: Bool,
                                      token: String?,
                                      resume: Bool = false)
        -> RemoteStreamingRepackOptions {
        defaultProfile.installOptions(
            outputDirectory: outputDirectory,
            overwrite: overwrite,
            token: token,
            resume: resume)
    }
}
