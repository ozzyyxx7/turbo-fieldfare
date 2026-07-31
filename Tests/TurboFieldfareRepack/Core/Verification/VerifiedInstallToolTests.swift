import Darwin
import Foundation
import Testing
@testable import TurboFieldfareRepackCore

@Suite struct VerifiedInstallToolTests {
    @Test func verifierRestoresAllowlistedReceiptSourceIdentity() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "verified-install-\(UUID().uuidString).gturbo",
                isDirectory: true)
        let expertsDirectory = root.appendingPathComponent(
            "packed_experts",
            isDirectory: true)
        try FileManager.default.createDirectory(
            at: expertsDirectory,
            withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        let expertStride = UInt64(getpagesize())
        let layerURL = expertsDirectory.appendingPathComponent("layer_00.bin")
        try Data(repeating: 0xA5, count: Int(expertStride)).write(to: layerURL)
        let layout: [String: Any] = [
            "expertStride": expertStride,
            "numLayers": 1,
            "expertsPerLayer": 1,
            "layers": [[
                "layer": 0,
                "file": "layer_00.bin",
                "experts": [[
                    "expert": 0,
                    "offset": 0,
                    "size": expertStride,
                ]],
            ]],
        ]
        let layoutData = try JSONSerialization.data(
            withJSONObject: layout,
            options: [.sortedKeys])
        let layoutURL = expertsDirectory.appendingPathComponent("layout.json")
        try layoutData.write(to: layoutURL)

        let source = SupportedModelSource.superGemma
        let manifest: [String: Any] = [
            "modelID": source.repoID,
            "sourceSnapshotHash": "sha256:" + source.sourceIndexSHA256,
            "expertsPerLayer": 1,
            "numLayers": 1,
            "expertStride": expertStride,
            "files": [
                "packed_experts/layout.json": [
                    "size": UInt64(layoutData.count),
                    "sha256": try Sha256Stream.hashFile(path: layoutURL.path),
                ],
                "packed_experts/layer_00.bin": [
                    "size": expertStride,
                    "sha256": try Sha256Stream.hashFile(path: layerURL.path),
                ],
            ],
        ]
        let manifestData = try JSONSerialization.data(
            withJSONObject: manifest,
            options: [.sortedKeys])
        try manifestData.write(to: root.appendingPathComponent("manifest.json"))

        _ = try VerifiedInstallTool.run(
            options: VerifyInstallOptions(inputGTurbo: root.path))

        let receipt = try JSONSerialization.jsonObject(
            with: Data(contentsOf:
                root.appendingPathComponent("verified-install.json")))
            as! [String: Any]
        #expect(receipt["sourceRepoID"] as? String == source.repoID)
        #expect(receipt["sourceRevision"] as? String == source.revision)
    }
}
