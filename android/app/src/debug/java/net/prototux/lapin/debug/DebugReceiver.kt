package net.prototux.lapin.debug

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.util.Log
import net.prototux.lapin.LapinApp
import net.prototux.lapin.TAG
import net.prototux.lapin.ui.AssistActivity
import net.prototux.lapin.voice.LapinVoiceInteractionService

/**
 * Debug builds only: drive the app from adb (logcat tag "Lapin").
 *
 *   adb shell am broadcast -p net.prototux.lapin -a net.prototux.lapin.DEBUG_CONFIG \
 *       --es url ws://10.0.2.2:8765/v1/device --es owner alice --es name "Test phone" --es device_id test-phone-1
 *   adb shell am broadcast -p net.prototux.lapin -a net.prototux.lapin.DEBUG_TEXT --es text "quelle heure est-il ?" [--ez speak true]
 *   adb shell am broadcast -p net.prototux.lapin -a net.prototux.lapin.DEBUG_ACTIVATE
 *   adb shell am broadcast -p net.prototux.lapin -a net.prototux.lapin.DEBUG_CONFIRM --ez yes true
 */
class DebugReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val a = LapinApp.assistant
        when (intent.action) {
            ACTION_CONFIG -> {
                intent.getStringExtra("url")?.let { a.prefs.url = it }
                intent.getStringExtra("name")?.let { a.prefs.name = it }
                intent.getStringExtra("owner")?.let { a.prefs.owner = it }
                a.prefs.overrideIdentity(intent.getStringExtra("device_id"), intent.getStringExtra("token"))
                Log.i(TAG, "DEBUG config: url=${a.prefs.url} id=${a.prefs.deviceId} owner=${a.prefs.owner}")
                a.settingsChanged()
                a.connectNow()
            }
            ACTION_TEXT -> {
                val text = intent.getStringExtra("text") ?: return
                val speak = intent.getBooleanExtra("speak", false)
                Log.i(TAG, "DEBUG text: $text")
                val pending = goAsync()
                val main = Handler(Looper.getMainLooper())
                var finished = false
                val finish = Runnable { if (!finished) { finished = true; pending.finish() } }
                main.postDelayed({ if (!finished) Log.w(TAG, "DEBUG reply: (timeout)"); finish.run() }, 55_000)
                a.sendText(text, speak) { reply ->
                    Log.i(TAG, "DEBUG reply: $reply")
                    main.post(finish)
                }
            }
            ACTION_ACTIVATE -> {
                Log.i(TAG, "DEBUG activate")
                if (!LapinVoiceInteractionService.show()) {
                    context.startActivity(Intent(context, AssistActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }
            ACTION_CONFIRM -> a.confirm(intent.getBooleanExtra("yes", true))
        }
    }

    companion object {
        const val ACTION_CONFIG = "net.prototux.lapin.DEBUG_CONFIG"
        const val ACTION_TEXT = "net.prototux.lapin.DEBUG_TEXT"
        const val ACTION_ACTIVATE = "net.prototux.lapin.DEBUG_ACTIVATE"
        const val ACTION_CONFIRM = "net.prototux.lapin.DEBUG_CONFIRM"
    }
}
