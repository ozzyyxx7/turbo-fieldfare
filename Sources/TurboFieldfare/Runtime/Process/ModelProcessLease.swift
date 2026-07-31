import Darwin
import Foundation

/// Cross-process ownership for the single local model runtime.
///
/// Every executable that loads a model holds this lease for the lifetime of
/// its loaded session. `flock` is released by the kernel if a process exits,
/// so stale metadata never keeps the model permanently locked.
public final class ModelProcessLease: @unchecked Sendable {
    public enum LeaseError: Error, CustomStringConvertible {
        case busy(owner: String?)
        case insecureLock(path: String)
        case posix(call: String, errno: Int32)

        public var description: String {
            switch self {
            case .busy(let owner):
                let detail = owner.map { " (\($0))" } ?? ""
                return "another TurboFieldfare model runtime is already active\(detail)"
            case .insecureLock(let path):
                return "model runtime lock path is not privately owned: \(path)"
            case .posix(let call, let code):
                return "\(call) failed with errno \(code)"
            }
        }
    }

    private let stateLock = NSLock()
    private var descriptor: Int32

    private init(descriptor: Int32) {
        self.descriptor = descriptor
    }

    deinit {
        release()
    }

    public static func acquire(
        owner: String = ProcessInfo.processInfo.processName,
        lockURL: URL = defaultLockURL()
    ) throws -> ModelProcessLease {
        let parent = lockURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: parent,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: NSNumber(value: 0o700)])
        try validatePrivateDirectory(path: parent.path)

        let path = lockURL.path
        let descriptor = Darwin.open(
            path,
            O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW,
            S_IRUSR | S_IWUSR)
        guard descriptor >= 0 else {
            throw LeaseError.posix(call: "open(\(path))", errno: errno)
        }

        var locked = false
        do {
            try validatePrivateRegularFile(descriptor: descriptor, path: path)
            guard flock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
                let savedErrno = errno
                let metadata = readMetadata(descriptor: descriptor)
                if savedErrno == EWOULDBLOCK || savedErrno == EAGAIN {
                    throw LeaseError.busy(owner: metadata)
                }
                throw LeaseError.posix(call: "flock(\(path))", errno: savedErrno)
            }
            locked = true
            try validatePrivateRegularFile(descriptor: descriptor, path: path)
            try writeMetadata(descriptor: descriptor, owner: owner)
            return ModelProcessLease(descriptor: descriptor)
        } catch {
            if locked { _ = flock(descriptor, LOCK_UN) }
            Darwin.close(descriptor)
            throw error
        }
    }

    public func release() {
        stateLock.lock()
        let descriptor = self.descriptor
        self.descriptor = -1
        stateLock.unlock()
        guard descriptor >= 0 else { return }
        _ = flock(descriptor, LOCK_UN)
        Darwin.close(descriptor)
    }

    public static func defaultLockURL() -> URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent("TurboFieldfare", isDirectory: true)
            .appendingPathComponent("model-owner.lock", isDirectory: false)
    }

    private static func validatePrivateRegularFile(
        descriptor: Int32,
        path: String
    ) throws {
        var info = stat()
        guard fstat(descriptor, &info) == 0 else {
            throw LeaseError.posix(call: "fstat(\(path))", errno: errno)
        }
        let fileType = info.st_mode & S_IFMT
        guard fileType == S_IFREG,
              info.st_uid == geteuid(),
              info.st_mode & 0o077 == 0 else {
            throw LeaseError.insecureLock(path: path)
        }

        var pathInfo = stat()
        guard lstat(path, &pathInfo) == 0 else {
            throw LeaseError.posix(call: "lstat(\(path))", errno: errno)
        }
        guard pathInfo.st_dev == info.st_dev,
              pathInfo.st_ino == info.st_ino else {
            throw LeaseError.insecureLock(path: path)
        }
    }

    private static func validatePrivateDirectory(path: String) throws {
        var info = stat()
        guard lstat(path, &info) == 0 else {
            throw LeaseError.posix(call: "lstat(\(path))", errno: errno)
        }
        guard info.st_mode & S_IFMT == S_IFDIR,
              info.st_uid == geteuid(),
              info.st_mode & 0o022 == 0 else {
            throw LeaseError.insecureLock(path: path)
        }
    }

    private static func readMetadata(descriptor: Int32) -> String? {
        guard lseek(descriptor, 0, SEEK_SET) >= 0 else { return nil }
        var bytes = [UInt8](repeating: 0, count: 1024)
        let count = Darwin.read(descriptor, &bytes, bytes.count)
        guard count > 0 else { return nil }
        let value = String(decoding: bytes.prefix(count), as: UTF8.self)
            .split(whereSeparator: \.isNewline)
            .map(String.init)
            .joined(separator: ", ")
        return value.isEmpty ? nil : value
    }

    private static func writeMetadata(descriptor: Int32, owner: String) throws {
        let safeOwner = owner
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
        let startedAt = ISO8601DateFormatter().string(from: Date())
        let data = Data("""
            pid=\(getpid())
            process=\(safeOwner)
            started_at=\(startedAt)

            """.utf8)
        guard ftruncate(descriptor, 0) == 0,
              lseek(descriptor, 0, SEEK_SET) >= 0 else {
            throw LeaseError.posix(call: "prepare model runtime lock", errno: errno)
        }
        try data.withUnsafeBytes { rawBuffer in
            guard var address = rawBuffer.baseAddress else { return }
            var remaining = rawBuffer.count
            while remaining > 0 {
                let written = Darwin.write(descriptor, address, remaining)
                if written < 0, errno == EINTR { continue }
                guard written > 0 else {
                    throw LeaseError.posix(call: "write model runtime lock", errno: errno)
                }
                remaining -= written
                address = address.advanced(by: written)
            }
        }
    }
}
