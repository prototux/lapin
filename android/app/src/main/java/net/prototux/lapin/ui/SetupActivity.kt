package net.prototux.lapin.ui

import android.Manifest
import android.app.NotificationManager
import android.app.role.RoleManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import net.prototux.lapin.LapinApp
import net.prototux.lapin.R
import net.prototux.lapin.core.Phase
import net.prototux.lapin.music.MediaListener
import net.prototux.lapin.music.MusicApps
import net.prototux.lapin.music.MusicPick
import net.prototux.lapin.net.ConnState
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.runtime.remember

/** Launcher activity: server, identity, pairing state, permissions, default assistant. */
class SetupActivity : ComponentActivity() {
    private var resumeTick by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val a = LapinApp.assistant
        if (a.prefs.configured) a.connectNow()
        setContent { LapinTheme { SetupScreen(resumeTick) } }
    }

    override fun onResume() {
        super.onResume()
        resumeTick++
    }
}

private fun phonePermissions(tv: Boolean): List<Pair<Int, List<String>>> {
    if (tv) return listOf(R.string.perm_mic to listOf(Manifest.permission.RECORD_AUDIO))
    return listOf(
        R.string.perm_mic to listOf(Manifest.permission.RECORD_AUDIO),
        R.string.perm_contacts to listOf(Manifest.permission.READ_CONTACTS),
        R.string.perm_sms to listOf(Manifest.permission.SEND_SMS),
        R.string.perm_phone to listOf(Manifest.permission.CALL_PHONE),
        R.string.perm_location to listOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
    )
}

@Composable
private fun SetupScreen(resumeTick: Int) {
    val ctx = LocalContext.current
    val assistant = LapinApp.assistant
    val prefs = assistant.prefs
    val conn by assistant.connState.collectAsState()
    val ui by assistant.ui.collectAsState()
    var url by rememberSaveable { mutableStateOf(prefs.url) }
    var name by rememberSaveable { mutableStateOf(prefs.name) }
    var owner by rememberSaveable { mutableStateOf(prefs.owner) }
    var testResult by rememberSaveable { mutableStateOf("") }
    var permTick by rememberSaveable { mutableIntStateOf(0) }
    val perms = phonePermissions(assistant.isTv)
    val testPhrase = stringResource(R.string.test_phrase)
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { permTick++ }
    // re-read permission / role state when coming back from the settings
    @Suppress("UNUSED_VARIABLE") val refresh = resumeTick + permTick
    fun granted(p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        Column(
            Modifier
                .align(Alignment.TopCenter)
                .widthIn(max = 720.dp)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .statusBarsPadding()
                .navigationBarsPadding()
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            // header
            Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 12.dp, bottom = 4.dp)) {
                Orb(ui.phase, ui.done, 0f, 0f, 64.dp,
                    stringResource(R.string.btn_try), { ctx.startActivity(Intent(ctx, AssistActivity::class.java)) })
                Spacer(Modifier.width(14.dp))
                Column {
                    Text(stringResource(R.string.app_name), style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.SemiBold)
                    Text(stringResource(R.string.setup_subtitle), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }

            // server
            Section(stringResource(R.string.section_server)) {
                OutlinedTextField(url, { url = it }, Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.field_url)) },
                    singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), shape = RoundedCornerShape(14.dp))
                OutlinedTextField(name, { name = it }, Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.field_name)) },
                    singleLine = true, shape = RoundedCornerShape(14.dp))
                OutlinedTextField(owner, { owner = it.lowercase().trim() }, Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.field_owner)) },
                    singleLine = true, shape = RoundedCornerShape(14.dp),
                    supportingText = { Text(stringResource(R.string.owner_help)) })
                StatusRow(conn)
                SelectionContainer {
                    Text(stringResource(R.string.device_id_label, prefs.deviceId), style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button(onClick = {
                        prefs.url = url; prefs.name = name; prefs.owner = owner
                        assistant.settingsChanged()
                        assistant.connectNow()
                    }) { Text(stringResource(R.string.save_connect)) }
                    FilledTonalButton(onClick = {
                        testResult = "…"
                        assistant.sendText(testPhrase, speak = true) { testResult = it }
                    }) { Text(stringResource(R.string.btn_test)) }
                }
                if (testResult.isNotEmpty()) Text(stringResource(R.string.test_reply, testResult), style = MaterialTheme.typography.bodyMedium)
                OutlinedButton(onClick = { ctx.startActivity(Intent(ctx, AssistActivity::class.java)) }) { Text(stringResource(R.string.btn_try)) }
            }

            // permissions
            Section(stringResource(R.string.section_permissions)) {
                Text(stringResource(R.string.perm_explain), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                for ((label, list) in perms) {
                    val ok = list.any { granted(it) }
                    PermRow(stringResource(label), ok) { launcher.launch(list.toTypedArray()) }
                }
                if (!assistant.isTv) {
                    val nm = ctx.getSystemService(NotificationManager::class.java)
                    PermRow(stringResource(R.string.perm_dnd), nm.isNotificationPolicyAccessGranted) {
                        open(ctx, Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
                    }
                }
                val missing = perms.flatMap { it.second }.filter { !granted(it) }
                if (missing.isNotEmpty()) Button(onClick = { launcher.launch(missing.toTypedArray()) }) { Text(stringResource(R.string.btn_grant_all)) }
            }

            // default assistant
            Section(stringResource(R.string.section_assistant)) {
                val isDefault = isDefaultAssistant(ctx)
                Text(stringResource(R.string.assistant_explain), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Dot(if (isDefault) Brand.Mint else Brand.Orange)
                    Spacer(Modifier.width(8.dp))
                    Text(stringResource(if (isDefault) R.string.assistant_is_default else R.string.assistant_not_default), style = MaterialTheme.typography.bodyMedium)
                }
                FilledTonalButton(onClick = {
                    open(ctx, Intent(Settings.ACTION_VOICE_INPUT_SETTINGS), Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS), Intent(Settings.ACTION_SETTINGS))
                }) { Text(stringResource(R.string.btn_assistant_settings)) }
            }

            if (!assistant.isTv) MusicSection(resumeTick + permTick)

            Section(stringResource(R.string.section_more)) {
                Text(stringResource(R.string.more_explain), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Spacer(Modifier.size(12.dp))
        }
    }
}

@Composable
private fun MusicSection(refresh: Int) {
    val ctx = LocalContext.current
    val prefs = LapinApp.assistant.prefs
    val apps = remember(refresh) { MusicApps(ctx).all() }
    var chosen by remember { mutableStateOf(prefs.musicApp) }
    var open by remember { mutableStateOf(false) }
    val auto = apps.firstOrNull { MusicPick.Tempus.isTempus(it.pkg) }?.label ?: stringResource(R.string.music_none)
    val autoLabel = stringResource(R.string.music_auto, auto)
    val current = apps.firstOrNull { it.pkg == chosen }?.label ?: autoLabel
    Section(stringResource(R.string.section_music)) {
        Text(stringResource(R.string.music_explain), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.music_app), Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
            Box {
                OutlinedButton(onClick = { open = true }) { Text(current) }
                DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                    DropdownMenuItem(text = { Text(autoLabel) }, onClick = { chosen = ""; prefs.musicApp = ""; open = false })
                    for (a in apps) DropdownMenuItem(text = { Text(a.label) }, onClick = { chosen = a.pkg; prefs.musicApp = a.pkg; open = false })
                }
            }
        }
        PermRow(stringResource(R.string.perm_media), MediaListener.granted(ctx)) {
            open(ctx, Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }
        Text(stringResource(R.string.perm_media_explain), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(
        shape = RoundedCornerShape(24.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.primary)
            content()
        }
    }
}

@Composable
private fun Dot(color: Color) = Box(Modifier.size(10.dp).background(color, CircleShape))

@Composable
private fun StatusRow(conn: ConnState) {
    val (color, text) = when (conn) {
        ConnState.Disconnected -> Brand.Slate to stringResource(R.string.status_disconnected)
        ConnState.Connecting -> Brand.Blue to stringResource(R.string.status_connecting)
        is ConnState.Pending -> Brand.Orange to stringResource(R.string.status_pending)
        is ConnState.Connected -> Brand.Mint to stringResource(R.string.status_connected, conn.server.ifEmpty { "Lapin" })
        is ConnState.Failed -> Brand.Red to stringResource(R.string.status_failed, conn.message)
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Dot(color)
        Spacer(Modifier.width(8.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
    }
}

@Composable
private fun PermRow(label: String, ok: Boolean, onGrant: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, Modifier.weight(1f), style = MaterialTheme.typography.bodyLarge)
        if (ok) {
            Icon(painterResource(R.drawable.ic_check), null, tint = Brand.Mint, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(6.dp))
            Text(stringResource(R.string.granted), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } else {
            TextButton(onClick = onGrant) { Text(stringResource(R.string.btn_grant)) }
        }
    }
}

private fun open(ctx: android.content.Context, vararg intents: Intent) {
    for (i in intents) {
        try { ctx.startActivity(i); return } catch (_: ActivityNotFoundException) { } catch (_: SecurityException) { }
    }
}

private fun isDefaultAssistant(ctx: android.content.Context): Boolean {
    if (Build.VERSION.SDK_INT >= 29) {
        val rm = ctx.getSystemService(RoleManager::class.java)
        if (rm != null && rm.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) return rm.isRoleHeld(RoleManager.ROLE_ASSISTANT)
    }
    val vis = Settings.Secure.getString(ctx.contentResolver, "voice_interaction_service") ?: return false
    return vis.startsWith(ctx.packageName + "/")
}
