import AppKit
import Carbon
import Darwin

struct Connection: Decodable { let server: String; let token: String }
struct CaptureReply { let text: String; let keepDraft: Bool }
func connection() throws -> Connection {
    let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/enmotion-robot-sync/config.json")
    return try JSONDecoder().decode(Connection.self, from: Data(contentsOf: path))
}
func capture(_ text: String, id: String, completion: @escaping (Result<CaptureReply, Error>) -> Void) {
    do {
        let config = try connection()
        guard let url = URL(string: config.server + "/api/sync/capture") else { throw NSError(domain: "配置地址无效", code: 1) }
        var request = URLRequest(url: url, timeoutInterval: 150)
        request.httpMethod = "POST"
        request.setValue("Bearer " + config.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text, "request_id": id])
        URLSession.shared.dataTask(with: request) { data, response, error in
            do {
                if let error = error { throw error }
                let value = try JSONSerialization.jsonObject(with: data ?? Data()) as? [String: Any] ?? [:]
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw NSError(domain: "Enmotion", code: 1, userInfo: [NSLocalizedDescriptionKey: value["detail"] as? String ?? "Jetson 暂时无法处理，请稍后重试"])
                }
                let agent = value["agent"] as? [String: Any]
                let steps = agent?["steps"] as? [[String: Any]] ?? []
                let failed = steps.contains { $0["ok"] as? Bool == false }
                completion(.success(CaptureReply(text: value["reply"] as? String ?? "已提交，请打开工作台查看结果", keepDraft: failed || value["fallback"] as? Bool == true)))
            } catch { completion(.failure(error)) }
        }.resume()
    } catch { completion(.failure(error)) }
}

class InputView: NSTextView {
    var submit: (() -> Void)?
    var hide: (() -> Void)?
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 36 && event.modifierFlags.contains(.command) { submit?(); return }
        if event.keyCode == 53 { hide?(); return }
        super.keyDown(with: event)
    }
}

class CaptureApp: NSObject, NSApplicationDelegate, NSTextViewDelegate {
    var item: NSStatusItem!
    var panel: NSPanel!
    let input = InputView()
    let feedback = NSTextView()
    let send = NSButton(title: "交给小助手  ⌘↩", target: nil, action: nil)
    let paste = NSButton(title: "粘贴文字", target: nil, action: nil)
    var hotKey: EventHotKeyRef?
    var handler: EventHandlerRef?
    var busy = false
    var requestID = UserDefaults.standard.string(forKey: "requestID") ?? UUID().uuidString

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(show), name: NSNotification.Name("com.enmotion.capture.show"), object: nil)
        buildPanel()
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "✦ 随手记"
        let menu = NSMenu()
        let open = NSMenuItem(title: "打开随手记（⌃⌥空格）", action: #selector(show), keyEquivalent: "")
        open.target = self; menu.addItem(open)
        let web = NSMenuItem(title: "打开工作台", action: #selector(openWeb), keyEquivalent: "")
        web.target = self; menu.addItem(web)
        menu.addItem(.separator())
        menu.addItem(withTitle: "退出", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "")
        item.menu = menu
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
            guard let context = context else { return OSStatus(eventNotHandledErr) }
            Unmanaged<CaptureApp>.fromOpaque(context).takeUnretainedValue().show()
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), &handler)
        let status = RegisterEventHotKey(UInt32(kVK_Space), UInt32(controlKey | optionKey), EventHotKeyID(signature: 0x454E4D4F, id: 1), GetApplicationEventTarget(), 0, &hotKey)
        NSLog("Enmotion quick capture ready; shortcut status=%d", status)
        if status != noErr { feedback.string = "快捷键已被占用，请从菜单栏打开随手记。" }
        if CommandLine.arguments.contains("--show") { show() }
    }
    func buildPanel() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 560, height: 510), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        panel.title = "Enmotion · 随手记"
        panel.isReleasedWhenClosed = false
        panel.level = .floating
        panel.center()
        panel.backgroundColor = NSColor(calibratedRed: 0.96, green: 0.95, blue: 0.99, alpha: 1)
        let root = panel.contentView!
        func label(_ text: String, size: CGFloat, y: CGFloat) {
            let view = NSTextField(labelWithString: text)
            view.font = .systemFont(ofSize: size, weight: size > 20 ? .semibold : .regular)
            view.frame = NSRect(x: 26, y: y, width: 508, height: 30)
            root.addSubview(view)
        }
        label("✦ 把工作放在这里", size: 25, y: 450)
        label("写一句话，小助手会整理待办或保存笔记。", size: 14, y: 416)
        label("例如：明天下午三点提醒我跟进客户报价", size: 12, y: 385)
        let scroll = NSScrollView(frame: NSRect(x: 26, y: 204, width: 508, height: 175))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        input.frame = NSRect(x: 0, y: 0, width: 490, height: 175)
        input.isVerticallyResizable = true
        input.autoresizingMask = [.width]
        input.textContainer?.widthTracksTextView = true
        input.textContainerInset = NSSize(width: 12, height: 12)
        input.isRichText = false
        input.font = .systemFont(ofSize: 16)
        input.delegate = self
        input.string = UserDefaults.standard.string(forKey: "draft") ?? ""
        input.submit = { [weak self] in self?.submit() }
        input.hide = { [weak self] in self?.panel.orderOut(nil) }
        scroll.documentView = input
        root.addSubview(scroll)
        paste.frame = NSRect(x: 24, y: 158, width: 105, height: 32)
        paste.bezelStyle = .rounded; paste.target = self; paste.action = #selector(pasteText)
        root.addSubview(paste)
        send.frame = NSRect(x: 344, y: 158, width: 192, height: 32)
        send.bezelStyle = .rounded; send.bezelColor = .systemPurple; send.target = self; send.action = #selector(submit)
        root.addSubview(send)
        let resultScroll = NSScrollView(frame: NSRect(x: 26, y: 46, width: 508, height: 100))
        resultScroll.hasVerticalScroller = true
        feedback.frame = NSRect(x: 0, y: 0, width: 490, height: 100)
        feedback.isEditable = false; feedback.isRichText = false; feedback.drawsBackground = false
        feedback.isVerticallyResizable = true; feedback.autoresizingMask = [.width]
        feedback.textContainer?.widthTracksTextView = true
        feedback.font = .systemFont(ofSize: 14)
        feedback.string = "⌃⌥空格 随时打开 · ⌘↩ 提交 · Esc 收起\n只在点击粘贴时读取剪贴板。"
        resultScroll.documentView = feedback; root.addSubview(resultScroll)
        let web = NSButton(title: "打开工作台 →", target: self, action: #selector(openWeb))
        web.frame = NSRect(x: 386, y: 9, width: 150, height: 30); web.bezelStyle = .inline
        root.addSubview(web)
    }
    @objc func show() {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(input)
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        show()
        return true
    }
    @objc func openWeb() {
        if let config = try? connection(), let url = URL(string: config.server) { NSWorkspace.shared.open(url) }
    }
    @objc func pasteText() {
        guard !busy, let text = NSPasteboard.general.string(forType: .string) else { return }
        input.insertText(text, replacementRange: input.selectedRange())
    }
    func textDidChange(_ notification: Notification) {
        requestID = UUID().uuidString
        saveDraft()
    }
    func saveDraft() {
        UserDefaults.standard.set(input.string, forKey: "draft")
        UserDefaults.standard.set(requestID, forKey: "requestID")
    }
    @objc func submit() {
        guard !busy else { return }
        let text = input.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.count <= 2200 else { feedback.string = "请输入 1–2200 字的工作内容。"; return }
        saveDraft()
        busy = true; send.isEnabled = false; paste.isEnabled = false; input.isEditable = false
        feedback.string = "小助手正在 Jetson 上处理，请稍等…"
        capture(text, id: requestID) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false; self.send.isEnabled = true; self.paste.isEnabled = true; self.input.isEditable = true
                switch result {
                case .success(let reply):
                    self.feedback.string = reply.text
                    if !reply.keepDraft { self.input.string = ""; self.requestID = UUID().uuidString; self.saveDraft() }
                case .failure(let error):
                    self.feedback.string = "提交未确认：\(error.localizedDescription)\n文字已保留，可以重试；同一次提交不会重复执行。"
                }
            }
        }
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if busy { feedback.string = "正在提交，请等处理结束后退出。"; show(); return .terminateCancel }
        return .terminateNow
    }
}

if CommandLine.arguments.contains("--smoke-test"), let text = CommandLine.arguments.last {
    capture(text, id: "mac-smoke-" + UUID().uuidString) { result in
        switch result {
        case .success(let reply): print(reply.text); exit(reply.keepDraft ? 2 : 0)
        case .failure(let error): fputs(error.localizedDescription + "\n", stderr); exit(1)
        }
    }
    RunLoop.main.run()
} else {
    let lockPath = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/enmotion-robot-sync/capture.lock").path
    let lockFD = Darwin.open(lockPath, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
    if lockFD < 0 { fputs("无法创建随手记实例锁\n", stderr); exit(1) }
    if flock(lockFD, LOCK_EX | LOCK_NB) != 0 {
        DistributedNotificationCenter.default().postNotificationName(NSNotification.Name("com.enmotion.capture.show"), object: nil, userInfo: nil, deliverImmediately: true)
        exit(0)
    }
    let app = NSApplication.shared
    let delegate = CaptureApp()
    app.delegate = delegate
    app.run()
}
