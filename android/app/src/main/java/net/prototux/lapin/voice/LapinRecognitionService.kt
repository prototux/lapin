package net.prototux.lapin.voice

import android.content.Intent
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/**
 * Required by the voice-interaction-service declaration. Speech recognition is
 * done by the server inside assistant turns; Lapin offers no system-wide
 * recognizer, so requests from other apps get an error.
 */
class LapinRecognitionService : RecognitionService() {
    override fun onStartListening(recognizerIntent: Intent?, listener: Callback?) {
        runCatching { listener?.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY) }
    }

    override fun onCancel(listener: Callback?) {}
    override fun onStopListening(listener: Callback?) {}
}
