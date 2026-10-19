import AVFoundation
import Observation
import Speech

/// Hold-to-talk dictation, recognised on the phone when it can be.
@MainActor @Observable
final class Dictation {
    private(set) var listening = false
    private(set) var transcript = ""
    private(set) var problem: String?
    private let engine = AVAudioEngine()
    private var task: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?

    func start() async {
        guard !listening else { return }
        problem = nil
        transcript = ""
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard speech == .authorized, await AVAudioApplication.requestRecordPermission() else {
            problem = "Allow speech recognition and the microphone in Settings to dictate."
            return
        }
        guard let recognizer = SFSpeechRecognizer(), recognizer.isAvailable else {
            problem = "Dictation isn't available right now."
            return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            nonisolated(unsafe) let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
            self.request = request
            let input = engine.inputNode
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in request.append(buffer) }
            engine.prepare()
            try engine.start()
            listening = true
            task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                let text = result?.bestTranscription.formattedString
                let finished = error != nil || (result?.isFinal ?? false)
                Task { @MainActor in
                    if let text { self?.transcript = text }
                    if finished { self?.stop() }
                }
            }
        } catch {
            problem = error.localizedDescription
            stop()
        }
    }

    func stop() {
        guard listening || engine.isRunning else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.finish()
        request = nil
        task = nil
        listening = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
