import Foundation
import Testing
@testable import TurboFieldfareRepackCore

@Suite struct SupportedModelSourceTests {
    @Test func exposesPinnedStockAndSuperGemmaProfiles() {
        #expect(SupportedModelSource.all == [
            SupportedModelSource.stock,
            SupportedModelSource.superGemma,
        ])
        #expect(SupportedModelSource.defaultProfile == SupportedModelSource.stock)

        let superGemma = SupportedModelSource.superGemma
        #expect(superGemma.id == "supergemma")
        #expect(superGemma.repoID ==
            "Jiunsong/supergemma4-26b-uncensored-mlx-4bit-v2")
        #expect(superGemma.revision ==
            "1ecb7582718b813fc4c7b5c3131b2b7053787f00")
        #expect(superGemma.sourceIndexSHA256 ==
            "df3133d5e9e400092664cb2197413a32035189ab7c41f4b000f75a284abdc512")
        #expect(superGemma.installFileName == "supergemma4.gturbo")
        #expect(Set(SupportedModelSource.all.map(\.id)).count ==
            SupportedModelSource.all.count)
        #expect(Set(SupportedModelSource.all.map(\.repoID)).count ==
            SupportedModelSource.all.count)
        #expect(Set(SupportedModelSource.all.map(\.installFileName)).count ==
            SupportedModelSource.all.count)
    }

    @Test func sourceAliasesResolveWithoutChangingTheDefault() {
        #expect(SupportedModelSource.profile(named: "stock") ==
            SupportedModelSource.stock)
        #expect(SupportedModelSource.profile(named: "SUPER") ==
            SupportedModelSource.superGemma)
        #expect(SupportedModelSource.profile(named: "supergemma4") ==
            SupportedModelSource.superGemma)
        #expect(SupportedModelSource.profile(
            named: SupportedModelSource.superGemma.repoID) ==
            SupportedModelSource.superGemma)
        #expect(SupportedModelSource.profile(named: "unknown") == nil)
        #expect(SupportedModelSource.repoID == SupportedModelSource.stock.repoID)
    }

    @Test func knownSourceRequiresRepositoryCommitAndIndexFingerprint() {
        let source = SupportedModelSource.superGemma
        #expect(SourceFingerprint.profile(
            repoID: source.repoID,
            indexSha256: source.sourceIndexSHA256.uppercased()) == source)
        #expect(SourceFingerprint.profile(
            repoID: source.repoID,
            resolvedCommit: source.revision,
            indexSha256: source.sourceIndexSHA256) == source)
        #expect(SourceFingerprint.profile(
            repoID: SupportedModelSource.stock.repoID,
            resolvedCommit: source.revision,
            indexSha256: source.sourceIndexSHA256) == nil)
        #expect(SourceFingerprint.profile(
            repoID: source.repoID,
            resolvedCommit: String(repeating: "0", count: 40),
            indexSha256: source.sourceIndexSHA256) == nil)
        #expect(SourceFingerprint.profile(
            repoID: source.repoID,
            resolvedCommit: source.revision,
            indexSha256: String(repeating: "0", count: 64)) == nil)
    }

    @Test func installOptionsUseTheSelectedProfile() {
        let output = URL(fileURLWithPath: "/tmp/supergemma.gturbo")
        let source = SupportedModelSource.superGemma
        let options = source.installOptions(
            outputDirectory: output,
            overwrite: false,
            token: nil,
            resume: true)

        #expect(options.repoID == source.repoID)
        #expect(options.revision == source.revision)
        #expect(options.outputDir == output.path)
        #expect(options.requireKnownSource)
        #expect(options.resume)
    }
}
