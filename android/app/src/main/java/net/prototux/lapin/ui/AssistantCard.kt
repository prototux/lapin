package net.prototux.lapin.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.MutableTransitionState
import androidx.compose.animation.core.spring
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import net.prototux.lapin.R
import net.prototux.lapin.core.Assistant
import net.prototux.lapin.core.Phase
import net.prototux.lapin.core.UiState
import androidx.compose.runtime.collectAsState

/** The card bound to the assistant. */
@Composable
fun AssistantOverlay(assistant: Assistant, onDismiss: () -> Unit, scrim: Boolean = true, large: Boolean = false) {
    val state by assistant.ui.collectAsState()
    val mic by assistant.micLevel.collectAsState()
    val voice by assistant.speakLevel.collectAsState()
    AssistantCard(
        state, mic, voice,
        onOrb = assistant::orbTap, onDismiss = onDismiss, onConfirm = assistant::confirm,
        onSend = { assistant.sendText(it, speak = false) }, onTyping = assistant::setTyping,
        scrim = scrim, large = large,
    )
}

@Composable
fun phaseLabel(s: UiState): String = when (s.phase) {
    Phase.IDLE -> if (s.done && s.reply.isBlank()) stringResource(R.string.state_done) else stringResource(R.string.state_idle)
    Phase.CONNECTING -> stringResource(R.string.state_connecting)
    Phase.PENDING -> stringResource(R.string.state_pending)
    Phase.LISTENING -> stringResource(R.string.state_listening)
    Phase.THINKING -> stringResource(R.string.state_thinking)
    Phase.SPEAKING -> stringResource(R.string.state_speaking)
    Phase.ERROR -> stringResource(R.string.state_error)
}

@Composable
fun AssistantCard(
    state: UiState, micLevel: Float, speakLevel: Float,
    onOrb: () -> Unit, onDismiss: () -> Unit, onConfirm: (Boolean) -> Unit,
    onSend: (String) -> Unit, onTyping: (Boolean) -> Unit,
    scrim: Boolean = true, large: Boolean = false,
) {
    val noRipple = remember { MutableInteractionSource() }
    val cardTap = remember { MutableInteractionSource() }
    val appear = remember { MutableTransitionState(false).apply { targetState = true } }
    Box(
        Modifier
            .fillMaxSize()
            .then(
                if (scrim) Modifier
                    .background(Brush.verticalGradient(listOf(Color.Transparent, Color.Black.copy(alpha = 0.42f))))
                    .clickable(noRipple, null) { onDismiss() }
                else Modifier,
            ),
    ) {
        AnimatedVisibility(
            appear,
            enter = slideInVertically(spring(dampingRatio = 0.82f, stiffness = 420f)) { it / 2 } + fadeIn(),
            modifier = Modifier.align(Alignment.BottomCenter),
        ) {
            Surface(
                modifier = Modifier
                    .widthIn(max = if (large) 900.dp else 640.dp)
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .imePadding()
                    .padding(10.dp)
                    .then(if (scrim) Modifier.clickable(cardTap, null) { } else Modifier),  // taps on the card don't dismiss
                shape = RoundedCornerShape(32.dp),
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                tonalElevation = 6.dp,
                shadowElevation = 18.dp,
            ) {
                CardContent(state, micLevel, speakLevel, onOrb, onDismiss, onConfirm, onSend, onTyping, large)
            }
        }
    }
}

@Composable
private fun CardContent(
    s: UiState, micLevel: Float, speakLevel: Float,
    onOrb: () -> Unit, onDismiss: () -> Unit, onConfirm: (Boolean) -> Unit,
    onSend: (String) -> Unit, onTyping: (Boolean) -> Unit, large: Boolean,
) {
    val scale = if (large) 1.35f else 1f
    Column(
        Modifier
            .padding(horizontal = 22.dp, vertical = 16.dp)
            .animateContentSize(spring(dampingRatio = 0.9f, stiffness = 500f)),
    ) {
        // handle
        Box(
            Modifier.align(Alignment.CenterHorizontally).width(36.dp).size(36.dp, 4.dp)
                .background(MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.3f), CircleShape),
        )
        Spacer(Modifier.size(12.dp))

        // what was heard (or typed)
        AnimatedContent(
            targetState = s.transcript.ifBlank { null },
            transitionSpec = { fadeIn() togetherWith fadeOut() }, label = "transcript",
        ) { t ->
            if (t != null) Text(
                t,
                style = MaterialTheme.typography.headlineSmall.copy(fontSize = (22 * scale).sp, lineHeight = (28 * scale).sp),
                fontWeight = FontWeight.Medium,
                fontStyle = if (s.transcriptFinal) FontStyle.Normal else FontStyle.Italic,
                color = if (s.transcriptFinal) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
            ) else Text(
                phaseLabel(s),
                style = MaterialTheme.typography.headlineSmall.copy(fontSize = (22 * scale).sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // the answer
        AnimatedVisibility(s.reply.isNotBlank(), enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Box(Modifier.padding(top = 10.dp).heightIn(max = (260 * scale).dp).verticalScroll(rememberScrollState())) {
                Text(
                    s.reply,
                    style = MaterialTheme.typography.bodyLarge.copy(fontSize = (17 * scale).sp, lineHeight = (24 * scale).sp),
                    color = MaterialTheme.colorScheme.onSurface,
                )
            }
        }

        // hint / error
        AnimatedVisibility(s.hint.isNotBlank(), enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Text(
                s.hint, Modifier.padding(top = 8.dp),
                style = MaterialTheme.typography.bodyMedium,
                color = if (s.phase == Phase.ERROR) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        // confirmation
        AnimatedVisibility(s.confirm != null, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            Column(Modifier.padding(top = 14.dp)) {
                Text(
                    (s.confirm ?: "").replaceFirstChar { it.uppercase() } + " ?",
                    style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.size(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedButton(onClick = { onConfirm(false) }, modifier = Modifier.weight(1f)) { Text(stringResource(R.string.no)) }
                    Button(onClick = { onConfirm(true) }, modifier = Modifier.weight(1f)) { Text(stringResource(R.string.yes)) }
                }
            }
        }

        // keyboard
        AnimatedVisibility(s.typing, enter = fadeIn() + expandVertically(), exit = fadeOut() + shrinkVertically()) {
            var text by remember { mutableStateOf("") }
            val focus = remember { FocusRequester() }
            LaunchedEffect(Unit) { runCatching { focus.requestFocus() } }
            val send = { if (text.isNotBlank()) { onSend(text); text = "" } }
            Row(Modifier.padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    text, { text = it },
                    modifier = Modifier.weight(1f).focusRequester(focus),
                    placeholder = { Text(stringResource(R.string.type_hint)) },
                    singleLine = true,
                    shape = RoundedCornerShape(24.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { send() }),
                )
                Spacer(Modifier.width(8.dp))
                FilledIconButton(onClick = send) { Icon(painterResource(R.drawable.ic_send), stringResource(R.string.cd_send)) }
            }
        }

        Spacer(Modifier.size(10.dp))
        // bottom bar: keyboard | orb | close
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { onTyping(!s.typing) }) {
                Icon(
                    painterResource(if (s.typing) R.drawable.ic_mic else R.drawable.ic_keyboard),
                    stringResource(R.string.cd_keyboard),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.weight(1f))
            Orb(
                s.phase, s.done, micLevel, speakLevel, (76 * scale).dp,
                stringResource(R.string.cd_orb), onOrb,
            )
            Spacer(Modifier.weight(1f))
            IconButton(onClick = onDismiss) {
                Icon(painterResource(R.drawable.ic_close), stringResource(R.string.cd_close), tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
