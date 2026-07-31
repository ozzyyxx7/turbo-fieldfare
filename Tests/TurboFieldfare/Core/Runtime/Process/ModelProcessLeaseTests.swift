import Foundation
import Testing
@testable import TurboFieldfare

@Suite(.serialized)
struct ModelProcessLeaseTests {
    @Test func excludesASecondOwnerAndRecoversAfterRelease() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("model-process-lease-\(UUID().uuidString)")
        let lockURL = directory.appendingPathComponent("model-owner.lock")
        defer { try? FileManager.default.removeItem(at: directory) }

        let first = try ModelProcessLease.acquire(owner: "first-test-owner", lockURL: lockURL)
        do {
            _ = try ModelProcessLease.acquire(owner: "second-test-owner", lockURL: lockURL)
            Issue.record("a second model owner acquired the same lock")
        } catch let error as ModelProcessLease.LeaseError {
            guard case .busy(let owner) = error else {
                Issue.record("unexpected lease error: \(error)")
                return
            }
            #expect(owner?.contains("first-test-owner") == true)
        }

        first.release()
        let recovered = try ModelProcessLease.acquire(
            owner: "recovered-test-owner",
            lockURL: lockURL)
        recovered.release()
    }

    @Test func rejectsAWorldReadableLockFile() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("model-process-lease-\(UUID().uuidString)")
        let lockURL = directory.appendingPathComponent("model-owner.lock")
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true)
        FileManager.default.createFile(
            atPath: lockURL.path,
            contents: Data(),
            attributes: [.posixPermissions: NSNumber(value: 0o644)])

        do {
            _ = try ModelProcessLease.acquire(owner: "test-owner", lockURL: lockURL)
            Issue.record("an insecure lock file was accepted")
        } catch let error as ModelProcessLease.LeaseError {
            guard case .insecureLock(let path) = error else {
                Issue.record("unexpected lease error: \(error)")
                return
            }
            #expect(path == lockURL.path)
        }
    }

    @Test func rejectsAWorldWritableParentDirectory() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("model-process-lease-\(UUID().uuidString)")
        let lockURL = directory.appendingPathComponent("model-owner.lock")
        defer { try? FileManager.default.removeItem(at: directory) }
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true)
        try FileManager.default.setAttributes(
            [.posixPermissions: NSNumber(value: 0o777)],
            ofItemAtPath: directory.path)

        do {
            _ = try ModelProcessLease.acquire(owner: "test-owner", lockURL: lockURL)
            Issue.record("an insecure parent directory was accepted")
        } catch let error as ModelProcessLease.LeaseError {
            guard case .insecureLock(let path) = error else {
                Issue.record("unexpected lease error: \(error)")
                return
            }
            #expect(path == directory.path)
        }
    }
}
