"""Incremental extraction of reply text from streamed structured model output."""
import json
import re


def reply_prefix(raw):
    match = re.search(r'"reply"\s*:\s*"', raw)
    if not match:
        return ""
    out, i = [], match.end()
    while i < len(raw):
        char = raw[i]
        if char == '"':
            break
        if char == '\\':
            if i+1 >= len(raw):
                break
            n = 6 if raw[i+1] == 'u' else 2
            if i+n > len(raw):
                break
            try:
                if n == 6 and 0xD800 <= int(raw[i+2:i+6],16) <= 0xDBFF:
                    if i+12 > len(raw) or raw[i+6:i+8] != '\\u':
                        break
                    n = 12
                out.append(json.loads('"'+raw[i:i+n]+'"'))
            except ValueError:
                break
            i += n
        else:
            out.append(char)
            i += 1
    return ''.join(out)


class SentenceEmitter:
    def __init__(self, callback):
        self.callback = callback
        self.sent = 0

    def accept(self, text, final=False):
        pending = text[self.sent:]
        while pending:
            boundaries = [m.end() for m in re.finditer(r'[。！？!?；;，,：:\n]', pending)
                          if m.end() >= 8 and not (m[0] in ':：' and m.start()>0
                              and pending[m.start()-1].isdigit())]
            size = boundaries[0] if boundaries and boundaries[0] <= 24 else 24 if len(pending) >= 24 else len(pending) if final else 0
            for clock in re.finditer(r'\d{1,2}[:：]\d{0,2}',pending):
                unfinished=clock.end()==len(pending) and len(clock[0].split(':')[-1].split('：')[-1])<2 and not final
                if clock.start()<size<clock.end() or (unfinished and clock.start()<size<=clock.end()):
                    size=clock.start() if clock.start()>=8 else 0
                    break
            if not final and size==len(pending):
                tail=re.search(r'\d+(?:[:：]\d*)?$',pending)
                if tail:size=tail.start() if tail.start()>=8 else 0
            if not size:
                return
            part = pending[:size]
            if part.strip():
                self.callback(part)
            self.sent += size
            pending = text[self.sent:]
