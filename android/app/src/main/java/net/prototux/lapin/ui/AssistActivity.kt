package net.prototux.lapin.ui

import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.addCallback
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import net.prototux.lapin.LapinApp
import net.prototux.lapin.core.Surface

/**
 * ACTION_ASSIST / ACTION_VOICE_COMMAND (headset long-press, fallback when
 * Lapin isn't the system assistant): the same card in a translucent activity.
 */
class AssistActivity : ComponentActivity(), Surface {
    private val assistant get() = LapinApp.assistant
    private var resumed = false
    override val isVisible: Boolean get() = resumed

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { LapinTheme { AssistantOverlay(assistant, onDismiss = ::userDismiss) } }
        onBackPressedDispatcher.addCallback(this) { userDismiss() }
        assistant.attach(this)
        if (savedInstanceState == null) assistant.activate("button")
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        assistant.attach(this)
        assistant.activate("button")
    }

    override fun onResume() { super.onResume(); resumed = true }
    override fun onPause() { super.onPause(); resumed = false }

    override fun onStop() {
        super.onStop()
        if (isFinishing || isChangingConfigurations) return
        // another app was opened by a tool: just go away; anything else (home...) is a dismissal
        if (SystemClock.elapsedRealtime() - assistant.lastExternalLaunch > 4000) assistant.dismissByUser()
        finish()
    }

    override fun onDestroy() {
        assistant.detach(this)
        super.onDestroy()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_SEARCH || keyCode == KeyEvent.KEYCODE_ASSIST || keyCode == KeyEvent.KEYCODE_VOICE_ASSIST) {
            assistant.orbTap(); return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun dismiss() = finish()

    private fun userDismiss() {
        assistant.dismissByUser()
        finish()
    }
}
