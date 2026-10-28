class SpeechCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channels = inputs[0];
    if (channels?.[0]) this.port.postMessage(channels[0].slice());
    return true;
  }
}
registerProcessor('speech-capture', SpeechCapture);
