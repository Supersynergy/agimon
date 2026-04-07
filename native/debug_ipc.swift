import Foundation

let home = FileManager.default.homeDirectoryForCurrentUser.path
let bin = "\(home)/.local/bin/agimon-core"

print("Binary exists: \(FileManager.default.fileExists(atPath: bin))")
print("Path: \(bin)")

let p = Process()
p.executableURL = URL(fileURLWithPath: bin)
p.arguments = ["ipc"]
let pipe = Pipe()
p.standardOutput = pipe
p.standardError = Pipe()
do {
    try p.run()
    p.waitUntilExit()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    let str = String(data: data, encoding: .utf8) ?? "nil"
    print("Exit code: \(p.terminationStatus)")
    print("Output length: \(data.count)")
    print("First 100 chars: \(String(str.prefix(100)))")
} catch {
    print("Error: \(error)")
}
