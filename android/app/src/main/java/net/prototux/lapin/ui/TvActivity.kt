package net.prototux.lapin.ui

import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import net.prototux.lapin.LapinApp
import net.prototux.lapin.R
import net.prototux.lapin.core.Phase
import net.prototux.lapin.core.Surface

/** Android TV: a big talk button (OK on the remote) and the same card. */
class TvActivity : ComponentActivity(), Surface {
    private val assistant get() = LapinApp.assistant
    private var resumed = false
    override val isVisible: Boolean get() = resumed

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { LapinTheme(dark = true) { TvScreen() } }
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        assistant.attach(this)
        assistant.connectNow()
    }

    override fun onPause() {
        super.onPause()
        resumed = false
        assistant.detach(this)
        // an app opened by a tool (open_url...) takes the screen: let the answer finish
        if (android.os.SystemClock.elapsedRealtime() - assistant.lastExternalLaunch > 4000) assistant.dismissByUser()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        when (keyCode) {
            KeyEvent.KEYCODE_SEARCH, KeyEvent.KEYCODE_ASSIST, KeyEvent.KEYCODE_VOICE_ASSIST -> { assistant.orbTap(); return true }
        }
        return super.onKeyDown(keyCode, event)
    }

    /** Auto-dismiss after an answer: clear the card, stay on the screen. */
    override fun dismiss() = assistant.dismissByUser()

    @Composable
    private fun TvScreen() {
        val s by assistant.ui.collectAsState()
        val mic by assistant.micLevel.collectAsState()
        val voice by assistant.speakLevel.collectAsState()
        val focus = remember { FocusRequester() }
        LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
        Box(
            Modifier.fillMaxSize().background(Brush.linearGradient(listOf(Color(0xFF14112A), Color(0xFF0B1B2B)))),
        ) {
            Column(
                Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("Lapin", style = MaterialTheme.typography.displayMedium, fontWeight = FontWeight.SemiBold, color = Color.White)
                Spacer(Modifier.size(28.dp))
                Orb(s.phase, s.done, mic, voice, 168.dp, stringResource(R.string.cd_orb), assistant::orbTap, Modifier.focusRequester(focus))
                Spacer(Modifier.size(20.dp))
                Text(stringResource(R.string.tv_press), style = MaterialTheme.typography.titleMedium, color = Color.White.copy(alpha = 0.7f))
            }
            val active = s.phase != Phase.IDLE || s.transcript.isNotBlank() || s.reply.isNotBlank() || s.typing || s.confirm != null
            AnimatedVisibility(active, enter = fadeIn(), exit = fadeOut()) {
                AssistantCard(
                    s, mic, voice,
                    onOrb = assistant::orbTap, onDismiss = assistant::dismissByUser, onConfirm = assistant::confirm,
                    onSend = { assistant.sendText(it, speak = false) }, onTyping = assistant::setTyping,
                    scrim = false, large = true,
                )
            }
        }
    }
}
