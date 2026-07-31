import Foundation
import TurboFieldfareRepackCore

public struct AppModelInstallDescriptor: Equatable, Sendable {
    public let id: String
    public let displayName: String
    public let shortDisplayName: String
    public let repoID: String
    public let revision: String
    public let sourceIndexSHA256: String
    public let approximateDownloadBytes: UInt64
    public let installedBytes: UInt64
    public let rangeStagingBytes: UInt64
    public let reserveBytes: UInt64
    public let installFileName: String
    public let apiModelID: String

    public init(displayName: String,
                repoID: String,
                revision: String,
                sourceIndexSHA256: String,
                approximateDownloadBytes: UInt64,
                installedBytes: UInt64,
                rangeStagingBytes: UInt64,
                reserveBytes: UInt64,
                id: String? = nil,
                shortDisplayName: String? = nil,
                installFileName: String = "gemma4.gturbo",
                apiModelID: String? = nil) {
        self.id = id ?? repoID
        self.displayName = displayName
        self.shortDisplayName = shortDisplayName ?? displayName
        self.repoID = repoID
        self.revision = revision
        self.sourceIndexSHA256 = sourceIndexSHA256
        self.approximateDownloadBytes = approximateDownloadBytes
        self.installedBytes = installedBytes
        self.rangeStagingBytes = rangeStagingBytes
        self.reserveBytes = reserveBytes
        self.installFileName = installFileName
        self.apiModelID = apiModelID ?? repoID
    }

    public var requiredFreeBytes: UInt64 {
        installedBytes + rangeStagingBytes + reserveBytes
    }

    public init(profile: SupportedModelSource.Profile) {
        self.init(
            displayName: profile.displayName,
            repoID: profile.repoID,
            revision: profile.revision,
            sourceIndexSHA256: profile.sourceIndexSHA256,
            approximateDownloadBytes: profile.approximateDownloadBytes,
            installedBytes: profile.installedBytes,
            rangeStagingBytes: UInt64(RemoteChunkPolicy.defaultBytes),
            reserveBytes: profile.reserveBytes,
            id: profile.id,
            shortDisplayName: profile.shortDisplayName,
            installFileName: profile.installFileName,
            apiModelID: profile.apiModelID)
    }

    public static let stock = AppModelInstallDescriptor(
        profile: SupportedModelSource.stock)
    public static let superGemma = AppModelInstallDescriptor(
        profile: SupportedModelSource.superGemma)
    public static let all = SupportedModelSource.all.map(
        AppModelInstallDescriptor.init(profile:))
    public static let `default` = stock
}

public struct AppModelInstallRequirement: Equatable, Sendable {
    public let probePath: String
    public let requiredBytes: UInt64
    public let availableBytes: UInt64

    public init(probePath: String = "", requiredBytes: UInt64, availableBytes: UInt64) {
        self.probePath = probePath
        self.requiredBytes = requiredBytes
        self.availableBytes = availableBytes
    }

    public var canInstall: Bool { availableBytes >= requiredBytes }

    public var shortfallBytes: UInt64 {
        requiredBytes > availableBytes ? requiredBytes - availableBytes : 0
    }
}

public enum AppModelInstallReadiness: Equatable, Sendable {
    case checking
    case ready(AppModelInstallRequirement)
    case insufficientSpace(AppModelInstallRequirement)
    case failed(String)

    public var requirement: AppModelInstallRequirement? {
        switch self {
        case .ready(let requirement), .insufficientSpace(let requirement):
            return requirement
        case .checking, .failed:
            return nil
        }
    }
}
