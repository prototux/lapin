package net.prototux.lapin.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import net.prototux.lapin.R
import net.prototux.lapin.core.Phase
import kotlin.math.sin

/** The animated listening indicator: ripples while listening, a spinning ring while thinking, voice bars while speaking. */
@Composable
fun Orb(
    phase: Phase, done: Boolean, micLevel: Float, speakLevel: Float,
    size: Dp, description: String, onClick: () -> Unit, modifier: Modifier = Modifier,
) {
    val inf = rememberInfiniteTransition(label = "orb")
    val spin by inf.animateFloat(0f, 360f, infiniteRepeatable(tween(if (phase == Phase.THINKING) 1100 else 6000, easing = LinearEasing)), label = "spin")
    val pulse by inf.animateFloat(0f, 1f, infiniteRepeatable(tween(1700, easing = LinearEasing)), label = "pulse")
    val breathe by inf.animateFloat(0f, 1f, infiniteRepeatable(tween(1400), RepeatMode.Reverse), label = "breathe")
    val wave by inf.animateFloat(0f, (2 * Math.PI).toFloat(), infiniteRepeatable(tween(900, easing = LinearEasing)), label = "wave")
    val mic by animateFloatAsState(micLevel, tween(90), label = "mic")
    val voice by animateFloatAsState(speakLevel, tween(70), label = "voice")
    val showDone = done && phase == Phase.IDLE
    val check by animateFloatAsState(if (showDone) 1f else 0f, spring(dampingRatio = 0.7f, stiffness = 300f), label = "check")
    val scale by animateFloatAsState(
        when (phase) { Phase.LISTENING -> 1f + 0.22f * mic; Phase.SPEAKING -> 1f + 0.1f * voice; Phase.THINKING -> 0.86f + 0.04f * breathe; else -> 0.92f },
        spring(stiffness = 600f), label = "scale",
    )
    val c1 by animateColorAsState(when {
        showDone -> Brand.Mint; phase == Phase.ERROR -> Brand.Red
        phase == Phase.PENDING || phase == Phase.CONNECTING -> Brand.Slate; else -> Brand.Violet
    }, tween(400), label = "c1")
    val c2 by animateColorAsState(when {
        showDone -> Brand.Teal; phase == Phase.ERROR -> Brand.Orange
        phase == Phase.PENDING || phase == Phase.CONNECTING -> Brand.Blue; phase == Phase.SPEAKING -> Brand.Cyan; else -> Brand.Blue
    }, tween(400), label = "c2")
    val c3 by animateColorAsState(when {
        showDone -> Brand.Cyan; phase == Phase.ERROR -> Brand.Pink
        phase == Phase.LISTENING -> Brand.Pink; else -> Brand.Cyan
    }, tween(400), label = "c3")

    Box(
        modifier
            .size(size)
            .clip(CircleShape)
            .clickable(remember { MutableInteractionSource() }, null) { onClick() }
            .semantics { contentDescription = description; role = Role.Button },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(Modifier.fillMaxSize()) {
            val r = this.size.minDimension / 2
            val core = r * 0.62f * scale
            // glow
            drawCircle(Brush.radialGradient(listOf(c1.copy(alpha = 0.32f), Color.Transparent), center, r), r)
            // ripples while listening (louder = stronger)
            if (phase == Phase.LISTENING) for (i in 0 until 3) {
                val p = (pulse + i / 3f) % 1f
                drawCircle(c3.copy(alpha = (1 - p) * (0.18f + 0.5f * mic)), core + (r - core) * p, style = Stroke(r * 0.035f))
            }
            if (phase == Phase.CONNECTING || phase == Phase.PENDING) {
                drawCircle(c2.copy(alpha = 0.25f + 0.25f * breathe), core + (r - core) * 0.5f * breathe, style = Stroke(r * 0.03f))
            }
            // the core: a slowly turning sweep gradient
            rotate(spin) {
                drawCircle(Brush.sweepGradient(listOf(c1, c2, c3, c1), center), core)
            }
            // thinking: a comet ring
            if (phase == Phase.THINKING) rotate(spin) {
                drawArc(
                    Brush.sweepGradient(listOf(Color.Transparent, c3, Color.White), center),
                    0f, 300f, false, topLeft = Offset(center.x - r * 0.84f, center.y - r * 0.84f),
                    size = Size(r * 1.68f, r * 1.68f), style = Stroke(r * 0.07f, cap = StrokeCap.Round),
                )
            }
            // a soft highlight
            drawCircle(
                Brush.radialGradient(listOf(Color.White.copy(alpha = 0.38f), Color.Transparent),
                    Offset(center.x - core * 0.35f, center.y - core * 0.4f), core * 0.9f),
                core,
            )
            // speaking: voice bars
            if (phase == Phase.SPEAKING) {
                val n = 4
                val w = core * 0.16f
                val gap = core * 0.12f
                val total = n * w + (n - 1) * gap
                for (i in 0 until n) {
                    val amp = 0.25f + 0.75f * (0.35f + 0.65f * voice) * (0.5f + 0.5f * sin(wave + i * 1.3f))
                    val h = core * 0.9f * amp
                    val x = center.x - total / 2 + i * (w + gap)
                    drawRoundRect(Color.White.copy(alpha = 0.92f), Offset(x, center.y - h / 2), Size(w, h), CornerRadius(w / 2))
                }
            }
            // done: an animated check mark
            if (check > 0.01f) {
                val path = Path().apply {
                    moveTo(center.x - core * 0.42f, center.y + core * 0.02f)
                    lineTo(center.x - core * 0.12f, center.y + core * 0.32f)
                    lineTo(center.x + core * 0.46f, center.y - core * 0.3f)
                }
                val pm = PathMeasure().apply { setPath(path, false) }
                val part = Path()
                pm.getSegment(0f, pm.length * check.coerceIn(0f, 1f), part, true)
                drawPath(part, Color.White, style = Stroke(core * 0.16f, cap = StrokeCap.Round, join = StrokeJoin.Round))
            }
        }
        if (!showDone && phase in setOf(Phase.IDLE, Phase.ERROR, Phase.PENDING)) {
            Icon(painterResource(R.drawable.ic_mic), null, tint = Color.White, modifier = Modifier.size(size * 0.3f))
        }
    }
}
