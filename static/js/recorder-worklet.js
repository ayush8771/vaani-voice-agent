// Passes raw microphone samples (Float32, mono) to the page for voice detection and WAV encoding.
class VaaniRecorder extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("vaani-recorder", VaaniRecorder);
