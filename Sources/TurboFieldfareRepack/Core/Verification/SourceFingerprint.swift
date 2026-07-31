import Foundation

/// Snapshot fingerprints pinned by the project. Adding a new entry means the
/// importer has been validated against a fresh upload of the source.
public enum SourceFingerprint {
    public static let knownFingerprints: [String: String] = Dictionary(
        uniqueKeysWithValues: SupportedModelSource.all.map {
            ($0.repoID, $0.sourceIndexSHA256)
        })

    /// Returns the recognised model ID for a given index.json SHA-256, or nil.
    public static func modelID(forIndexSha256 sha256Hex: String) -> String? {
        SupportedModelSource.all.first {
            $0.sourceIndexSHA256 == sha256Hex
        }?.repoID
    }

    /// Resolves the source identity recorded in a manifest.
    public static func profile(repoID: String,
                               indexSha256: String)
        -> SupportedModelSource.Profile? {
        SupportedModelSource.all.first {
            $0.repoID == repoID
                && $0.sourceIndexSHA256.lowercased()
                    == indexSha256.lowercased()
        }
    }

    /// Binds an allowlisted snapshot to its repository and immutable commit.
    public static func profile(repoID: String,
                               resolvedCommit: String,
                               indexSha256: String)
        -> SupportedModelSource.Profile? {
        guard let profile = profile(
            repoID: repoID,
            indexSha256: indexSha256),
              profile.revision == resolvedCommit else {
            return nil
        }
        return profile
    }
}
