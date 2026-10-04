import AppKit
import ServiceManagement

// Transpose: a menu-bar front end for the two scripts bundled in Contents/Resources.
// It runs them with the system /usr/bin/python3 and shows their output in a window.

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var resultWindow: NSWindow?
    private var resultTextView: NSTextView?
    private var currentTask = false

    private static let resources: String = Bundle.main.resourcePath ?? ""
    private let switchScriptPath: String = AppDelegate.resources + "/switch_account.py"
    private let cardScriptPath: String = AppDelegate.resources + "/account_sessions.py"

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            if let sfSymbol = NSImage(systemSymbolName: "person.2.circle", accessibilityDescription: "Transpose") {
                button.image = sfSymbol
            } else {
                button.title = "T"
            }
        }

        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu

        setupResultWindow()
    }

    func menuWillOpen(_ menu: NSMenu) {
        menu.removeAllItems()

        let statusItem = NSMenuItem(title: "Checking…", action: nil, keyEquivalent: "")
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        menu.addItem(NSMenuItem.separator())

        let switchAccountItem = NSMenuItem(title: "Move Sessions and Groups to This Account", action: #selector(switchToAccount), keyEquivalent: "")
        menu.addItem(switchAccountItem)

        let dryRunItem = NSMenuItem(title: "Check (Dry Run)", action: #selector(checkAccount), keyEquivalent: "")
        menu.addItem(dryRunItem)

        let allAccountsItem = NSMenuItem(title: "Gather Sessions from All Accounts", action: #selector(mergeAllAccounts), keyEquivalent: "")
        menu.addItem(allAccountsItem)

        let cardItem = NSMenuItem(title: "Account Census", action: #selector(listAccounts), keyEquivalent: "")
        menu.addItem(cardItem)

        menu.addItem(NSMenuItem.separator())

        menu.addItem(NSMenuItem(title: "Open Claude", action: #selector(openClaudeApp), keyEquivalent: ""))

        let launchAtLoginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        launchAtLoginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(launchAtLoginItem)

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit Transpose", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))

        // Update the status line asynchronously from a read-only check.
        Task {
            let output = await runScript(switchScriptPath, arguments: ["--check"], showResult: false)
            let lines = output.split(separator: "\n").filter { !$0.hasPrefix("❌ exit") }
            // A STOP line (the app is not on another account yet) outranks the target line.
            let stop = lines.first { $0.hasPrefix("STOP") }
            let line = (stop ?? lines.first ?? "").prefix(80)
            statusItem.title = (stop != nil ? "⚠️ " : "✅ ") + String(line)
        }

        // Disable actions while a script is running.
        let isRunning = currentTask
        switchAccountItem.isEnabled = !isRunning
        dryRunItem.isEnabled = !isRunning
        allAccountsItem.isEnabled = !isRunning
        cardItem.isEnabled = !isRunning
    }

    private func setupResultWindow() {
        resultWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 640, height: 420),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered, defer: false)
        resultWindow?.center()
        resultWindow?.isReleasedWhenClosed = false
        resultWindow?.level = .floating

        let scrollView = NSScrollView()
        scrollView.hasVerticalScroller = true
        scrollView.autoresizingMask = [.width, .height]

        resultTextView = NSTextView()
        resultTextView?.isEditable = false
        resultTextView?.isSelectable = true
        resultTextView?.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        resultTextView?.autoresizingMask = [.width, .height]
        resultTextView?.textContainerInset = NSSize(width: 10, height: 10)

        scrollView.documentView = resultTextView
        resultWindow?.contentView = scrollView
    }

    private func showResultWindow(with title: String, output: String) {
        resultWindow?.title = title
        resultTextView?.string = output
        resultWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func runScript(_ script: String, arguments: [String], confirmMessage: String? = nil, showResult: Bool = true, title: String = "Transpose") async -> String {
        if let msg = confirmMessage {
            let alert = NSAlert()
            alert.messageText = msg
            alert.alertStyle = .warning
            alert.addButton(withTitle: "OK")
            alert.addButton(withTitle: "Cancel")
            let response = alert.runModal()
            if response != .alertFirstButtonReturn {
                return "Cancelled."
            }
        }

        currentTask = true
        let output = await Task.detached { AppDelegate.execute(script, arguments) }.value
        currentTask = false

        if showResult {
            showResultWindow(with: title, output: output)
        }
        return output
    }

    nonisolated static func execute(_ script: String, _ arguments: [String]) -> String {
        guard FileManager.default.fileExists(atPath: script) else {
            return "❌ script not found in the app bundle: \((script as NSString).lastPathComponent)"
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [script] + arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.standardInput = FileHandle.nullDevice
        do {
            try process.run()
        } catch {
            return "❌ \(error.localizedDescription)"
        }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        let text = String(data: data, encoding: .utf8) ?? ""
        return process.terminationStatus == 0 ? text : "❌ exit \(process.terminationStatus)\n" + text
    }

    @objc private func switchToAccount(_ sender: NSMenuItem) {
        Task {
            _ = await runScript(switchScriptPath, arguments: ["--yes"], confirmMessage: "Claude will quit and reopen.", title: "Move to This Account")
        }
    }

    @objc private func checkAccount(_ sender: NSMenuItem) {
        Task {
            _ = await runScript(switchScriptPath, arguments: ["--check"], showResult: true, title: "Check (Dry Run)")
        }
    }

    @objc private func mergeAllAccounts(_ sender: NSMenuItem) {
        Task {
            _ = await runScript(switchScriptPath, arguments: ["--yes", "--all-accounts", "--allow-same"], confirmMessage: "This copies the sessions of every account into the account the app is signed in to. Claude will quit and reopen.", title: "Gather Sessions")
        }
    }

    @objc private func listAccounts(_ sender: NSMenuItem) {
        Task {
            _ = await runScript(cardScriptPath, arguments: ["card"], showResult: true, title: "Account Census")
        }
    }

    @objc private func openClaudeApp(_ sender: NSMenuItem) {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Claude.app"))
    }

    @objc private func toggleLaunchAtLogin(_ sender: NSMenuItem) {
        if SMAppService.mainApp.status == .enabled {
            do {
                try SMAppService.mainApp.unregister()
                sender.state = .off
            } catch {
                print("Failed to unregister launch at login: \(error)")
            }
        } else {
            do {
                try SMAppService.mainApp.register()
                sender.state = .on
            } catch {
                print("Failed to register launch at login: \(error)")
            }
        }
    }
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
