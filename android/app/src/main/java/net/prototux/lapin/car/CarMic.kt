package net.prototux.lapin.car

import android.Manifest
import android.content.pm.PackageManager
import androidx.car.app.CarContext
import androidx.car.app.annotations.ExperimentalCarApi
import androidx.car.app.media.CarAudioRecord
import net.prototux.lapin.audio.MicSource

/** The car's microphone through the host (car API level 5+). 16 kHz mono s16le, like the protocol wants. */
class CarMic(private val carContext: CarContext) : MicSource {
    private var rec: CarAudioRecord? = null

    @androidx.annotation.OptIn(ExperimentalCarApi::class)
    override fun start(): Boolean = try {
        if (carContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
            throw SecurityException("no microphone permission")
        val r = CarAudioRecord.create(carContext)
        r.startRecording()
        rec = r
        true
    } catch (e: Exception) {
        false
    }

    override fun read(buf: ByteArray, off: Int, len: Int): Int = rec?.read(buf, off, len) ?: -1

    override fun stop() {
        runCatching { rec?.stopRecording() }
        rec = null
    }
}
