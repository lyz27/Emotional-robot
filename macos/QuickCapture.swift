import AppKit
import Carbon
import Darwin

struct Connection: Decodable { let server: String; let token: String }
struct CaptureReply { let text: String; let keepDraft: Bool; let retryFresh: Bool }
func connection() throws -> Connection {
    let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/enmotion-robot-sync/config.json")
    return try JSONDecoder().decode(Connection.self, from: Data(contentsOf: path))
}
func capture(_ text: String, id: String, mode: String = "chat", completion: @escaping (Result<CaptureReply, Error>) -> Void) {
    do {
        let config = try connection()
        guard let url = URL(string: config.server + "/api/sync/capture") else { throw NSError(domain: "配置地址无效", code: 1) }
        var request = URLRequest(url: url, timeoutInterval: 150)
        request.httpMethod = "POST"
        request.setValue("Bearer " + config.token, forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["text": text, "request_id": id, "mode": mode])
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
                let keep = failed || value["fallback"] as? Bool == true
                let wrote = steps.contains { $0["ok"] as? Bool == true && ["create_task", "update_task", "save_note", "update_note"].contains($0["operation"] as? String ?? "") }
                completion(.success(CaptureReply(text: value["reply"] as? String ?? "已提交，请打开工作台查看结果", keepDraft: keep, retryFresh: keep && !wrote)))
            } catch { completion(.failure(error)) }
        }.resume()
    } catch { completion(.failure(error)) }
}

func workRequest(_ path: String = "/api/sync/work", body: [String: Any]? = nil, completion: @escaping (Result<[String: Any], Error>) -> Void) {
    do {
        let config = try connection()
        guard let url = URL(string: config.server + path) else { throw NSError(domain: "配置地址无效", code: 1) }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.setValue("Bearer " + config.token, forHTTPHeaderField: "Authorization")
        if let body = body {
            request.httpMethod = "PATCH"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        URLSession.shared.dataTask(with: request) { data, response, error in
            let result: Result<[String: Any], Error>
            do {
                if let error = error { throw error }
                let value = try JSONSerialization.jsonObject(with: data ?? Data()) as? [String: Any] ?? [:]
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw NSError(domain: "Enmotion", code: 1, userInfo: [NSLocalizedDescriptionKey: value["detail"] as? String ?? "无法读取工作记录"])
                }
                result = .success(value)
            } catch { result = .failure(error) }
            DispatchQueue.main.async { completion(result) }
        }.resume()
    } catch { DispatchQueue.main.async { completion(.failure(error)) } }
}

class RecordButton: NSButton { var recordID = 0; var recordText = "" }

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
    let modes = NSSegmentedControl(labels: ["随手记", "记笔记", "拆任务", "做复盘"], trackingMode: .selectOne, target: nil, action: nil)
    let sections = NSSegmentedControl(labels: ["待办", "最近笔记"], trackingMode: .selectOne, target: nil, action: nil)
    let listScroll = NSScrollView()
    let listStatus = NSTextField(labelWithString: "正在连接 Jetson…")
    let hint = NSTextField(labelWithString: "")
    var tasks: [[String: Any]] = []
    var notes: [[String: Any]] = []
    var refreshing = false
    var changingTask = false
    var refreshTimer: Timer?
    let modeValues = ["chat", "notes", "breakdown", "review"]
    var hotKey: EventHotKeyRef?
    var handler: EventHandlerRef?
    var busy = false
    var requestID = UserDefaults.standard.string(forKey: "requestID") ?? UUID().uuidString

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(show), name: NSNotification.Name("com.enmotion.capture.show"), object: nil)
        buildPanel()
        refreshTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.refreshWork()
        }
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
        refreshWork()
        if CommandLine.arguments.contains("--show") { show() }
    }
    func buildPanel() {
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 920, height: 560), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        panel.title = "Enmotion · 随手记"
        panel.appearance = NSAppearance(named: .aqua)
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
        label("✦ 你的随身工作助手", size: 25, y: 505)
        label("记住事情，也一起把工作往前推进。", size: 14, y: 472)
        modes.frame = NSRect(x: 26, y: 433, width: 508, height: 30)
        modes.selectedSegment = min(3, max(0, UserDefaults.standard.integer(forKey: "mode")))
        modes.target = self; modes.action = #selector(modeChanged)
        root.addSubview(modes)
        hint.frame = NSRect(x: 26, y: 392, width: 508, height: 34)
        hint.font = .systemFont(ofSize: 12); hint.maximumNumberOfLines = 2
        root.addSubview(hint)
        updateHint()
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
        let separator = NSBox(frame: NSRect(x: 555, y: 26, width: 1, height: 503))
        separator.boxType = .separator; root.addSubview(separator)
        let heading = NSTextField(labelWithString: "工作就在手边")
        heading.font = .systemFont(ofSize: 21, weight: .semibold)
        heading.frame = NSRect(x: 582, y: 505, width: 305, height: 30); root.addSubview(heading)
        sections.frame = NSRect(x: 582, y: 466, width: 228, height: 30)
        sections.selectedSegment = 0; sections.target = self; sections.action = #selector(renderWork)
        root.addSubview(sections)
        let refresh = NSButton(title: "刷新", target: self, action: #selector(refreshWork))
        refresh.frame = NSRect(x: 817, y: 466, width: 76, height: 30); refresh.bezelStyle = .rounded
        root.addSubview(refresh)
        listStatus.frame = NSRect(x: 582, y: 421, width: 305, height: 38)
        listStatus.font = .systemFont(ofSize: 12); listStatus.maximumNumberOfLines = 2
        root.addSubview(listStatus)
        listScroll.frame = NSRect(x: 578, y: 47, width: 316, height: 366)
        listScroll.hasVerticalScroller = true; listScroll.drawsBackground = false
        root.addSubview(listScroll)
        let footer = NSTextField(labelWithString: "每 30 秒同步 · 勾选即可完成待办")
        footer.frame = NSRect(x: 582, y: 17, width: 315, height: 24)
        footer.font = .systemFont(ofSize: 11); footer.textColor = .secondaryLabelColor
        root.addSubview(footer)
    }
    @objc func show() {
        NSApp.activate(ignoringOtherApps: true)
        panel.makeKeyAndOrderFront(nil)
        panel.makeFirstResponder(input)
        refreshWork()
    }
    func updateHint() {
        hint.stringValue = ["明天下午三点提醒我跟进客户报价", "粘贴会议内容或工作想法，保存成笔记。", "输入一个目标，得到步骤与第一步；不会直接创建任务。", "结合已有待办和笔记，复盘进度、卡点与下一步。只给建议。"][modes.selectedSegment]
    }
    @objc func modeChanged() {
        requestID = UUID().uuidString
        UserDefaults.standard.set(modes.selectedSegment, forKey: "mode")
        updateHint(); saveDraft()
    }
    @objc func refreshWork() {
        guard !refreshing, !changingTask else { return }
        refreshing = true
        workRequest { [weak self] result in
            guard let self = self else { return }
            self.refreshing = false
            switch result {
            case .success(let value):
                self.tasks = value["tasks"] as? [[String: Any]] ?? []
                self.notes = value["notes"] as? [[String: Any]] ?? []
                self.listStatus.stringValue = "\(self.tasks.count) 项未完成 · 已与 Jetson 同步"
                self.item?.button?.title = "✦ 随手记 · \(self.tasks.count)"
                self.item?.button?.toolTip = self.tasks.isEmpty ? "没有未完成待办" : self.tasks.prefix(3).map { $0["title"] as? String ?? "" }.joined(separator: "\n")
                self.renderWork()
            case .failure(let error):
                self.listStatus.stringValue = "连接失败，列表可能不是最新。点击刷新重试。"
                self.listStatus.toolTip = error.localizedDescription
                self.item?.button?.title = "✦ 随手记 · 离线"
            }
        }
    }
    func dueLabel(_ value: String?) -> String {
        guard let value = value else { return "未设时间" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = parser.date(from: value)
        if date == nil { parser.formatOptions = [.withInternetDateTime]; date = parser.date(from: value) }
        guard let due = date else { return value }
        let format = DateFormatter(); format.dateFormat = "M月d日 HH:mm"
        format.timeZone = TimeZone(identifier: "Asia/Shanghai")
        return (due < Date() ? "已逾期 · " : "") + format.string(from: due) + " 北京时间"
    }
    @objc func renderWork() {
        let isTasks = sections.selectedSegment == 0
        let records = isTasks ? tasks : notes
        let document = NSView(frame: NSRect(x: 0, y: 0, width: 298, height: max(366, records.count * 88)))
        listScroll.documentView = document
        if records.isEmpty {
            let empty = NSTextField(wrappingLabelWithString: isTasks ? "暂时没有未完成事项。\n在左侧写下接下来想做的事吧。" : "还没有工作笔记。\n用‘记笔记’留住想法和会议信息。")
            empty.frame = NSRect(x: 6, y: document.frame.height - 90, width: 285, height: 70)
            empty.textColor = .secondaryLabelColor; document.addSubview(empty)
        }
        for (index, record) in records.enumerated() {
            let y = document.frame.height - CGFloat((index + 1) * 88)
            let title = record["title"] as? String ?? "未命名"
            let button = RecordButton()
            button.recordID = record["id"] as? Int ?? 0
            button.target = self; button.isEnabled = !changingTask
            button.frame = NSRect(x: 3, y: y + 43, width: 288, height: 34)
            if isTasks {
                button.setButtonType(.switch); button.title = title
                button.action = #selector(completeTask(_:))
            } else {
                button.title = "▤ " + title; button.bezelStyle = .inline
                button.alignment = .left
                button.recordText = title + "\n\n" + (record["body"] as? String ?? "")
                button.action = #selector(readNote(_:))
            }
            button.lineBreakMode = .byTruncatingTail; button.toolTip = title
            document.addSubview(button)
            let detail = NSTextField(labelWithString: isTasks ? dueLabel(record["due"] as? String) : String((record["body"] as? String ?? "").replacingOccurrences(of: "\n", with: " ").prefix(45)))
            detail.frame = NSRect(x: isTasks ? 25 : 8, y: y + 13, width: 265, height: 28)
            detail.font = .systemFont(ofSize: 11); detail.textColor = .secondaryLabelColor
            detail.maximumNumberOfLines = 2; document.addSubview(detail)
        }
        document.scroll(NSPoint(x: 0, y: max(0, document.frame.height - listScroll.contentSize.height)))
    }
    @objc func readNote(_ sender: RecordButton) { feedback.string = sender.recordText }
    @objc func completeTask(_ sender: RecordButton) {
        guard !changingTask, !refreshing else { sender.state = .off; return }
        changingTask = true; renderWork()
        workRequest("/api/sync/tasks/\(sender.recordID)", body: ["done": true]) { [weak self] result in
            guard let self = self else { return }
            self.changingTask = false
            switch result {
            case .success: self.refreshWork()
            case .failure(let error):
                self.listStatus.stringValue = "完成状态未确认，请刷新后查看。"
                self.listStatus.toolTip = error.localizedDescription
                self.renderWork()
            }
        }
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
        busy = true; send.isEnabled = false; paste.isEnabled = false; input.isEditable = false; modes.isEnabled = false
        feedback.string = "小助手正在 Jetson 上处理，请稍等…"
        capture(text, id: requestID, mode: modeValues[modes.selectedSegment]) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.busy = false; self.send.isEnabled = true; self.paste.isEnabled = true; self.input.isEditable = true; self.modes.isEnabled = true
                switch result {
                case .success(let reply):
                    self.feedback.string = reply.text
                    if !reply.keepDraft { self.input.string = ""; self.requestID = UUID().uuidString; self.saveDraft() }
                    else if reply.retryFresh { self.requestID = UUID().uuidString; self.saveDraft() }
                    self.refreshWork()
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

if CommandLine.arguments.contains("--check-work") {
    workRequest { result in
        switch result {
        case .success(let value):
            print("Pending tasks: \((value["tasks"] as? [Any])?.count ?? 0); recent notes: \((value["notes"] as? [Any])?.count ?? 0)")
            exit(0)
        case .failure(let error): fputs(error.localizedDescription + "\n", stderr); exit(1)
        }
    }
    RunLoop.main.run()
} else if CommandLine.arguments.contains("--check-layout") {
    _ = NSApplication.shared
    let delegate = CaptureApp()
    delegate.buildPanel()
    let root = delegate.panel.contentView!
    for view in root.subviews { precondition(root.bounds.contains(view.frame), "View extends outside panel: \(view.frame)") }
    precondition(delegate.modes.segmentCount == 4)
    delegate.tasks = [["id": 1, "title": "布局验证", "due": "2026-11-07T15:00:00+08:00"]]
    delegate.renderWork()
    precondition(delegate.listScroll.documentView!.subviews.count == 2)
    delegate.sections.selectedSegment = 1
    delegate.notes = [["id": 1, "title": "笔记验证", "body": "会议记录"]]
    delegate.renderWork()
    precondition(delegate.listScroll.documentView!.subviews.count == 2)
    print("Two-column layout bounds and task/note rows verified")
    exit(0)
} else if CommandLine.arguments.contains("--smoke-test"), let text = CommandLine.arguments.last {
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
