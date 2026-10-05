package net.prototux.lapin.ui

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

object Brand {
    val Violet = Color(0xFF7C5CFF)
    val Blue = Color(0xFF5B8DEF)
    val Cyan = Color(0xFF22D3EE)
    val Pink = Color(0xFFF472B6)
    val Mint = Color(0xFF34D399)
    val Teal = Color(0xFF14B8A6)
    val Red = Color(0xFFF87171)
    val Orange = Color(0xFFFB923C)
    val Slate = Color(0xFF94A3B8)
}

private val Light = lightColorScheme(
    primary = Color(0xFF6246EA), onPrimary = Color.White,
    secondary = Color(0xFF0E7490), tertiary = Color(0xFFDB2777),
    background = Color(0xFFF8F7FC), surface = Color(0xFFFFFFFF),
    surfaceContainerHigh = Color(0xFFF1EFF8), surfaceContainer = Color(0xFFF4F2FA),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFB4A5FF), onPrimary = Color(0xFF24145F),
    secondary = Color(0xFF67E8F9), tertiary = Color(0xFFF9A8D4),
    background = Color(0xFF0F0E17), surface = Color(0xFF16151F),
    surfaceContainerHigh = Color(0xFF221F2E), surfaceContainer = Color(0xFF1C1A27),
)

@Composable
fun LapinTheme(dark: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val ctx = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(ctx) else dynamicLightColorScheme(ctx)
        dark -> Dark
        else -> Light
    }
    MaterialTheme(colorScheme = scheme, content = content)
}
