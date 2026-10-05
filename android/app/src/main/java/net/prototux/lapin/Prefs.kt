package net.prototux.lapin

import android.content.Context
import android.os.Build
import android.util.Base64
import androidx.core.content.edit
import java.security.SecureRandom
import java.util.UUID

/** Settings and the device identity (generated once, kept forever). */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("lapin", Context.MODE_PRIVATE)

    var url: String
        get() = sp.getString("url", null) ?: DEFAULT_URL
        set(v) = sp.edit { putString("url", v.trim()) }

    var name: String
        get() = sp.getString("name", null) ?: defaultName()
        set(v) = sp.edit { putString("name", v.trim()) }

    var owner: String
        get() = sp.getString("owner", null) ?: ""
        set(v) = sp.edit { putString("owner", v.trim().lowercase()) }

    /** Package of the music player app; "" = automatic (Tempus if installed). */
    var musicApp: String
        get() = sp.getString("music_app", null) ?: ""
        set(v) = sp.edit { putString("music_app", v) }

    val deviceId: String
        get() = sp.getString("device_id", null) ?: ("phone-" + UUID.randomUUID()).also {
            sp.edit { putString("device_id", it) }
        }

    val token: String
        get() = sp.getString("token", null) ?: newToken().also { sp.edit { putString("token", it) } }

    /** Debug builds only: pair as another device (tests use ids starting with "test-"). */
    fun overrideIdentity(deviceId: String?, token: String?) {
        sp.edit {
            if (!deviceId.isNullOrBlank()) putString("device_id", deviceId.trim())
            if (!token.isNullOrBlank()) putString("token", token.trim())
            else if (!deviceId.isNullOrBlank()) putString("token", newToken())
        }
    }

    val configured: Boolean get() = sp.contains("url")

    companion object {
        const val DEFAULT_URL = "ws://assistant.local:8765/v1/device"

        fun defaultName(): String {
            val model = Build.MODEL ?: "Android"
            val maker = Build.MANUFACTURER ?: ""
            return if (maker.isNotEmpty() && !model.lowercase().startsWith(maker.lowercase()))
                "${maker.replaceFirstChar { it.uppercase() }} $model" else model
        }

        private fun newToken(): String {
            val b = ByteArray(32).also { SecureRandom().nextBytes(it) }
            return Base64.encodeToString(b, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        }
    }
}
