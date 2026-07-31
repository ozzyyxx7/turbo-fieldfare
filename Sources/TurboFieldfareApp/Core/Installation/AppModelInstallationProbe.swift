import Foundation
import TurboFieldfare

public enum AppModelInstallationStatus: Equatable, Sendable {
    case missing
    case partial(String)
    case complete
}

public enum AppModelInstallationProbe {
    public static func status(
        at directory: URL,
        descriptor: AppModelInstallDescriptor = .default
    ) -> AppModelInstallationStatus {
        let directory = directory.standardizedFileURL
        let manifestURL = directory.appendingPathComponent("manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path) else {
            return .missing
        }

        do {
            let manifest = try ManifestReader.load(directoryURL: directory, expecting: .gemma4_26B_A4B)
            let expectedSource = "sha256:" + descriptor.sourceIndexSHA256
            guard manifest.modelID == descriptor.repoID,
                  manifest.sourceSnapshotHash == expectedSource else {
                return .partial("installed checkpoint does not match \(descriptor.displayName)")
            }
            let layout = directory.appendingPathComponent("packed_experts/layout.json")
            guard FileManager.default.fileExists(atPath: layout.path) else {
                return .partial("packed_experts/layout.json is missing")
            }
            for relativePath in [
                "tokenizer/tokenizer.json",
                "tokenizer/tokenizer_config.json",
            ] {
                guard let expected = manifest.files[relativePath] else {
                    return .partial("\(relativePath) is missing from manifest.json")
                }
                let fileURL = directory.appendingPathComponent(relativePath)
                guard FileManager.default.fileExists(atPath: fileURL.path) else {
                    return .partial("\(relativePath) is missing")
                }
                let values = try fileURL.resourceValues(forKeys: [.fileSizeKey])
                guard let fileSize = values.fileSize, fileSize >= 0,
                      UInt64(fileSize) == expected.size else {
                    return .partial("\(relativePath) has the wrong size")
                }
                let actualHash = try Sha256Verifier.hashFile(
                    at: fileURL,
                    chunkBytes: 65_536)
                guard actualHash.lowercased() == expected.sha256.lowercased() else {
                    return .partial("\(relativePath) SHA-256 does not match manifest.json")
                }
            }
            let receipt = try VerifiedInstallReceiptReader.load(directoryURL: directory)
            let currentSourceIdentity =
                receipt.sourceRepoID == descriptor.repoID
                    && receipt.sourceRevision == descriptor.revision
            let legacyVerifiedIdentity =
                receipt.sourceRepoID == nil
                    && receipt.sourceRevision?.lowercased()
                        == expectedSource.lowercased()
            guard currentSourceIdentity || legacyVerifiedIdentity else {
                return .partial(
                    "verified install source does not match \(descriptor.displayName)")
            }
            let manifestHash = try Sha256Verifier.hashFile(at: manifestURL, chunkBytes: 65_536)
            try VerifiedInstallReceiptReader.validateManifestBinding(
                receipt,
                directoryURL: directory,
                manifestSha256: manifestHash)
            return .complete
        } catch {
            return .partial("\(error)")
        }
    }
}
