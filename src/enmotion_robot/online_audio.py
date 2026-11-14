"""Online ASR and conservative text-aware endpoint decisions."""
import re
from pathlib import Path


class EndpointPolicy:
    def ready(self, text, silence, stable):
        text = re.sub(r'[\s，,。.!！?？]', '', text)
        if not text or stable < .25:
            return False
        if any(text.endswith(x) for x in ('等一下','让我想想','我还没说完','稍等')):
            return silence >= 4
        unfinished = any(text.endswith(x) for x in ('因为','所以','然后','但是','还有','如果','比如','我想','帮我','给我','和','以及','关于','的','把','首先','其次'))
        if unfinished or len(text) <= 3:
            return silence >= 2.2
        if any(text.endswith(x) for x in ('吗','呢','怎么做','是什么','几点','多少','谢谢')):
            return silence >= .7
        return silence >= 1.3


class OnlineSpeech:
    def __init__(self, root: Path):
        import sherpa_onnx
        self.model = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(root/'tokens.txt'), encoder=str(root/'encoder-epoch-99-avg-1.int8.onnx'),
            decoder=str(root/'decoder-epoch-99-avg-1.onnx'), joiner=str(root/'joiner-epoch-99-avg-1.int8.onnx'),
            num_threads=1, decoding_method='greedy_search', enable_endpoint_detection=False)
        self.reset()

    def reset(self):
        self.stream = self.model.create_stream()

    def accept(self, raw):
        import numpy as np
        self.stream.accept_waveform(16000,np.frombuffer(raw,dtype=np.int16).astype(np.float32)/32768)
        while self.model.is_ready(self.stream):
            self.model.decode_stream(self.stream)
        return self.model.get_result(self.stream).strip()

    def finish(self):
        # Flush lookahead as samples, not an additional wall-clock silence wait.
        self.accept(bytes(32000//2))
        self.stream.input_finished()
        while self.model.is_ready(self.stream):
            self.model.decode_stream(self.stream)
        return self.model.get_result(self.stream).strip()
