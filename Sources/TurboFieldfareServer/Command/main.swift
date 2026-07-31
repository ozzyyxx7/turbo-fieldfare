import Darwin
import Foundation
import TurboFieldfare
import TurboFieldfareServerCore

let arguments: ServerArguments
do {
    arguments = try ServerArguments.parse(Array(CommandLine.arguments.dropFirst()))
} catch ServerArgumentError.help {
    print(ServerArguments.usage)
    exit(0)
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n\n\(ServerArguments.usage)\n".utf8))
    exit(2)
}

do {
    let modelLease = try ModelProcessLease.acquire(owner: "TurboFieldfareServer")
    defer { modelLease.release() }
    let modelURL = URL(fileURLWithPath: arguments.model).standardizedFileURL
    let backend = try await ServerModelSession.load(
        modelDirectory: modelURL,
        maxContext: arguments.maxContext,
        modelProcessLease: modelLease,
        promptCacheMode: arguments.promptCacheMode)
    // Keep the default SIGINT/SIGTERM action while the model is loading so a
    // failed managed startup can terminate promptly. Graceful signal handling
    // is needed only after the backend is ready to serve requests.
    let signals = ServerTerminationSignals()
    let bearerToken = ProcessInfo.processInfo.environment["TURBOFIELDFARE_BEARER_TOKEN"]
        .flatMap { $0.isEmpty ? nil : $0 }
    let server = TurboFieldfareHTTPServer(
        modelID: arguments.modelID,
        queueLimit: arguments.queueLimit,
        backend: backend,
        bearerToken: bearerToken)
    _ = try await server.start(port: arguments.port)
    print("TurboFieldfareServer ready at http://127.0.0.1:\(arguments.port) model=\(arguments.modelID) context=\(arguments.maxContext) prompt_cache=\(arguments.promptCacheMode.rawValue)")

    _ = await signals.wait()
    try await server.shutdown()
    await signals.cancel()
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
