package net.prototux.lapin.core

import android.Manifest
import android.app.UiModeManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import net.prototux.lapin.BuildConfig
import net.prototux.lapin.Prefs
import net.prototux.lapin.R
import net.prototux.lapin.TAG
import net.prototux.lapin.audio.AudioRecordSource
import net.prototux.lapin.audio.Earcons
import net.prototux.lapin.audio.Focus
import net.prototux.lapin.audio.Mic
import net.prototux.lapin.audio.MicSource
import net.prototux.lapin.audio.Player
import net.prototux.lapin.net.ConnState
import net.prototux.lapin.net.Connection
import net.prototux.lapin.protocol.Proto
import net.prototux.lapin.protocol.ServerMsg
import net.prototux.lapin.tools.DeviceTools
import net.prototux.lapin.tools.ToolSpecs
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

enum class Phase { IDLE, CONNECTING, PENDING, LISTENING, THINKING, SPEAKING, ERROR }

data class UiState(
    val phase: Phase = Phase.IDLE,
    val transcript: String = "",
    val transcriptFinal: Boolean = false,
    val reply: String = "",
    /** Show a check mark: the action was done without a sentence (ack / "done" earcon). */
    val done: Boolean = false,
    val hint: String = "",
    /** Pending confirmation question: show Yes/No. */
    val confirm: String? = null,
    /** The keyboard is open. */
    val typing: Boolean = false,
)

/** Something showing the assistant: the voice session card, the assist activity, the TV screen, the car screen. */
interface Surface {
    val isVisible: Boolean
    fun dismiss()
    /** Starts an activity for a tool; false to let the assistant start it from the app context. */
    fun launch(intent: Intent): Boolean = false
    /** Navigation the surface handles itself (the car screen); false if not. */
    fun navigate(destination: String, mode: String): Boolean = false
}

/**
 * The turn logic: activation, microphone streaming, server messages,
 * playback, confirmations, typed requests and device tools. Main thread.
 */
class Assistant(private val app: Context) {
    private val main = Handler(Looper.getMainLooper())
    val prefs = Prefs(app)

    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui
    private val _micLevel = MutableStateFlow(0f)
    val micLevel: StateFlow<Float> = _micLevel

    val player = Player { id, ev -> connection.send(Proto.playback(id, ev)) }
    val speakLevel: StateFlow<Float> get() = player.level
    private val earcons = Earcons()
    private val focus = Focus(app)
    private val tools = DeviceTools(app, this)
    private val toolExec = Executors.newCachedThreadPool()

    val connection = Connection(
        url = { prefs.url }, hello = ::hello, onMessage = ::onMessage, onWire = ::onWire,
        onAudio = ::onAudio, onDown = ::onDown,
    )
    val connState: StateFlow<ConnState> get() = connection.state

    val isTv: Boolean = (app.getSystemService(UiModeManager::class.java)?.currentModeType == Configuration.UI_MODE_TYPE_TELEVISION) ||
        app.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)

    // microphone
    private var micFactory: () -> MicSource = { AudioRecordSource() }
    private var carMode = false
    private var mic: Mic? = null
    @Volatile private var streaming = false
    @Volatile private var buffering = false
    private val preBuffer = ArrayDeque<ByteArray>()
    @Volatile private var dropUntil = 0L

    // turn state
    private var wakeId = 0
    private var activeWake = 0
    private var turnSeq = 0
    private var pendingSource: String? = null
    private var pendingText: Pair<String, Boolean>? = null
    private var confirmEnds = 0
    /** The current turn was typed: answer as text, never reopen the mic. */
    private var typedTurn = false
    private val replyCallbacks = mutableListOf<(String) -> Unit>()

    private val surfaces = mutableListOf<Surface>()
    val surface: Surface? get() = surfaces.lastOrNull()
    /** When a tool last started another app (its window hiding ours is not a dismissal). */
    @Volatile var lastExternalLaunch = 0L

    private val idleClose = object : Runnable {
        override fun run() {
            val busy = phase !in setOf(Phase.IDLE, Phase.ERROR, Phase.PENDING) || player.anyActive ||
                pendingSource != null || pendingText != null
            if (busy) keepAlive() else { Log.i(TAG, "idle: closing the connection"); connection.close() }
        }
    }
    private val connectTimeout = Runnable {
        if (pendingSource != null || pendingText != null) {
            pendingSource = null; pendingText = null
            stopMic()
            val why = (connState.value as? ConnState.Failed)?.message
            fail(app.getString(R.string.hint_cant_reach, prefs.url) + if (why != null) "\n$why" else "")
        }
    }
    private val autoDismiss = Runnable {
        val s = _ui.value
        if (s.confirm == null && !s.typing && s.phase in setOf(Phase.IDLE, Phase.ERROR)) surface?.dismiss()
    }

    private val phase: Phase get() = _ui.value.phase

    // ------------------------------------------------------------------ surfaces
    fun attach(s: Surface) { surfaces.remove(s); surfaces += s }
    fun detach(s: Surface) { surfaces.remove(s) }

    fun enterCar(source: () -> MicSource) {
        carMode = true
        micFactory = source
        player.speechUsage = AudioAttributes.USAGE_MEDIA
    }

    fun exitCar() {
        carMode = false
        micFactory = { AudioRecordSource() }
        player.speechUsage = AudioAttributes.USAGE_ASSISTANT
    }

    // ------------------------------------------------------------------ actions
    /** Connects now (setup screen), and keeps the connection for a while. */
    fun connectNow() { connection.connect(); keepAlive() }

    fun settingsChanged() { connection.restart(); keepAlive() }

    /** Assistant key / orb tap / follow-up: start a voice turn. */
    fun activate(source: String = "button") {
        main.removeCallbacks(autoDismiss)
        if (source == "button" && phase == Phase.LISTENING) { endTurnByHand(); return }
        turnSeq++
        typedTurn = false
        player.stopSpeech()
        stopMic()
        pendingText = null
        if (source == "button") clearConfirm()
        keepAlive()
        _ui.update { it.copy(transcript = "", transcriptFinal = false, reply = "", done = false, hint = "", typing = false) }
        if (!hasMicPermission()) { fail(app.getString(R.string.hint_mic_permission)); return }
        (connState.value as? ConnState.Pending)?.let {
            setPhase(Phase.PENDING, app.getString(R.string.hint_pending, prefs.deviceId)); return
        }
        earcons.play(if (source == "followup") "followup" else "wake")
        dropUntil = SystemClock.elapsedRealtime() + 180      // don't send our own blip
        focus.request(exclusive = carMode)
        if (connection.isReady) {
            startMic()
            startTurn(source)
        } else {
            pendingSource = source
            buffering = true            // record while connecting: the first words aren't lost
            startMic()
            setPhase(Phase.CONNECTING)
            connection.connect()
            main.removeCallbacks(connectTimeout)
            main.postDelayed(connectTimeout, CONNECT_TIMEOUT_MS)
        }
    }

    /** The orb was tapped. */
    fun orbTap() {
        when (phase) {
            Phase.IDLE, Phase.ERROR, Phase.THINKING, Phase.SPEAKING -> activate("button")   // speaking: barge-in
            Phase.LISTENING -> endTurnByHand()
            Phase.CONNECTING -> dismissByUser()
            Phase.PENDING -> connectNow()
        }
    }

    /**
     * A typed request (keyboard, debug receiver, test button). From the keyboard
     * it's text only (`speak` false): typing is usually to stay quiet.
     */
    fun sendText(text: String, speak: Boolean = false, onReply: ((String) -> Unit)? = null) {
        val t = text.trim()
        if (t.isEmpty()) return
        main.removeCallbacks(autoDismiss)
        turnSeq++
        typedTurn = !speak
        clearConfirm()
        player.stopSpeech()
        stopMic()
        activeWake = 0
        if (onReply != null) replyCallbacks += onReply
        keepAlive()
        _ui.update { it.copy(phase = Phase.THINKING, transcript = t, transcriptFinal = true, reply = "", done = false, hint = "", typing = false) }
        if (connection.isReady) {
            connection.send(Proto.text(t, speak))
        } else {
            (connState.value as? ConnState.Pending)?.let {
                setPhase(Phase.PENDING, app.getString(R.string.hint_pending, prefs.deviceId))
                failReplies("pending approval: ${it.message}")
                return
            }
            pendingText = t to speak
            connection.connect()
            main.removeCallbacks(connectTimeout)
            main.postDelayed(connectTimeout, CONNECT_TIMEOUT_MS)
        }
    }

    /** Yes/No buttons of a confirmation. */
    fun confirm(yes: Boolean) {
        turnSeq++
        clearConfirm()
        player.stopSpeech()
        stopMic()
        activeWake = 0          // the follow-up turn the server aborts for the answer isn't ours anymore
        if (connection.send(Proto.confirmReply(yes))) setPhase(Phase.THINKING)
        else fail(app.getString(R.string.hint_connection_lost))
    }

    fun setTyping(on: Boolean) {
        if (on) {
            main.removeCallbacks(autoDismiss)
            if (phase == Phase.LISTENING) { cancelTurn(); setPhase(Phase.IDLE) }
        }
        _ui.update { it.copy(typing = on) }
    }

    /** Back / tap outside / card closed: abort the turn, stop speaking. */
    fun dismissByUser() {
        main.removeCallbacks(autoDismiss)
        main.removeCallbacks(connectTimeout)
        turnSeq++
        cancelTurn()
        pendingSource = null
        pendingText = null
        player.stopSpeech()
        _ui.value = UiState()
        focus.abandon()
        sendState("idle")
        keepAlive()
    }

    // ------------------------------------------------------------------ turn internals
    private fun cancelTurn() {
        if (phase in setOf(Phase.LISTENING, Phase.THINKING, Phase.SPEAKING) && connection.isReady)
            connection.send(Proto.button("cancel"))
        stopMic()
        activeWake = 0
    }

    private fun startTurn(source: String) {
        wakeId++
        activeWake = wakeId
        connection.send(Proto.wake(wakeId, source))
        synchronized(preBuffer) {
            for (f in preBuffer) connection.sendBinary(Proto.micFrame(f))
            preBuffer.clear()
            streaming = true
            buffering = false
        }
        player.setMicOpen(true)
        setPhase(Phase.LISTENING)
        sendState("listening")
    }

    private fun endTurnByHand() {
        if (activeWake != 0) connection.send(Proto.audioEnd(activeWake))
        stopMic()
        setPhase(Phase.THINKING)
        sendState("thinking")
    }

    private fun hasMicPermission() =
        app.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun startMic() {
        if (mic != null) return
        mic = Mic(micFactory(), ::onMicFrame, { _micLevel.value = it }, {
            main.post {
                if (mic != null) {
                    cancelTurn()
                    pendingSource = null
                    fail(app.getString(R.string.hint_mic_unavailable))
                }
            }
        }).also { it.start() }
    }

    private fun stopMic() {
        streaming = false
        buffering = false
        mic?.shutdown()
        mic = null
        synchronized(preBuffer) { preBuffer.clear() }
        player.setMicOpen(false)
        _micLevel.value = 0f
    }

    private fun onMicFrame(frame: ByteArray) {
        if (SystemClock.elapsedRealtime() < dropUntil) return
        if (streaming) { connection.sendBinary(Proto.micFrame(frame)); return }
        if (!buffering) return
        synchronized(preBuffer) {
            if (streaming) connection.sendBinary(Proto.micFrame(frame))
            else {
                preBuffer.addLast(frame)
                while (preBuffer.size > 250) preBuffer.removeFirst()    // 5 s
            }
        }
    }

    private fun matches(id: Int?) = activeWake != 0 && (id == null || id == activeWake)

    private fun setPhase(p: Phase, hint: String? = null) {
        _ui.update { it.copy(phase = p, hint = hint ?: if (p == it.phase) it.hint else "") }
    }

    private fun fail(message: String) {
        setPhase(Phase.ERROR, message)
        focus.abandon()
        failReplies(message)
    }

    private fun failReplies(message: String) {
        val cbs = replyCallbacks.toList(); replyCallbacks.clear()
        cbs.forEach { it("ERROR: $message") }
    }

    private fun clearConfirm() { confirmEnds = 0; _ui.update { it.copy(confirm = null) } }

    private fun sendState(s: String) { connection.send(Proto.state(s)) }

    private fun keepAlive() {
        main.removeCallbacks(idleClose)
        main.postDelayed(idleClose, KEEP_ALIVE_MS)
    }

    private fun scheduleDismiss(ms: Long) {
        main.removeCallbacks(autoDismiss)
        main.postDelayed(autoDismiss, ms)
    }

    // ------------------------------------------------------------------ server
    private fun hello(): JSONObject {
        val fr = Locale.getDefault().language == "fr"
        val specs = if (isTv) ToolSpecs.tv() else ToolSpecs.phone(fr)
        return Proto.hello(
            prefs.deviceId, prefs.token, if (isTv) "tv" else "phone", prefs.name, prefs.owner,
            BuildConfig.VERSION_NAME, ToolSpecs.toJson(specs), volumePercent(),
        )
    }

    private fun onAudio(data: ByteArray) {
        val f = Proto.parsePlayback(data) ?: return
        player.write(f.streamId, data, f.offset, f.length)
    }

    private fun onDown() {
        player.stopAll()
        if (phase in setOf(Phase.LISTENING, Phase.THINKING, Phase.SPEAKING)) {
            stopMic()
            activeWake = 0
            fail(app.getString(R.string.hint_connection_lost))
        } else if (phase == Phase.PENDING) {
            setPhase(Phase.IDLE)
        }
    }

    /** Socket thread: playback stream control must stay in order with the binary frames. */
    private fun onWire(msg: ServerMsg) {
        when (msg) {
            is ServerMsg.StreamOpen -> player.open(msg.id, msg.kind, msg.rate, msg.channels, msg.gainDb, msg.prebufferMs)
            is ServerMsg.StreamClose -> player.close(msg.id, msg.drain)
            is ServerMsg.StreamPause -> player.pause(msg.id, msg.paused)
            else -> {}
        }
    }

    private fun onMessage(msg: ServerMsg) {
        when (msg) {
            is ServerMsg.Welcome -> {
                Log.i(TAG, "connected to ${msg.server}")
                main.removeCallbacks(connectTimeout)
                if (phase == Phase.PENDING) setPhase(Phase.IDLE)
                pendingSource?.let { pendingSource = null; startTurn(it) }
                pendingText?.let { pendingText = null; connection.send(Proto.text(it.first, it.second)) }
            }
            is ServerMsg.Pending -> {
                Log.i(TAG, "pending: ${msg.message}")
                if (pendingSource != null || pendingText != null || phase == Phase.CONNECTING) {
                    stopMic()
                    pendingSource = null; pendingText = null
                    main.removeCallbacks(connectTimeout)
                    setPhase(Phase.PENDING, app.getString(R.string.hint_pending, prefs.deviceId))
                    failReplies("pending approval: ${msg.message}")
                }
            }
            is ServerMsg.Error -> {
                Log.w(TAG, "server error: ${msg.message}")
                main.removeCallbacks(connectTimeout)
                stopMic()
                pendingSource = null; pendingText = null
                if (phase != Phase.IDLE || surface?.isVisible == true) fail(msg.message) else failReplies(msg.message)
            }
            is ServerMsg.WakeAck -> {}
            is ServerMsg.Eot -> if (matches(msg.wakeId)) { stopMic(); setPhase(Phase.THINKING); sendState("thinking") }
            is ServerMsg.Cancel -> if (matches(msg.wakeId)) {
                stopMic()
                activeWake = 0
                val hint = when (msg.reason) {
                    "no_speech", "empty" -> app.getString(R.string.hint_heard_nothing)
                    "button" -> ""
                    else -> app.getString(R.string.hint_cancelled)
                }
                setPhase(Phase.IDLE, hint)
                if (!player.speechActive) focus.abandon()
                sendState("idle")
                scheduleDismiss(3500)
            }
            is ServerMsg.Transcript -> _ui.update { it.copy(transcript = msg.text, transcriptFinal = msg.final) }
            is ServerMsg.Reply -> {
                Log.i(TAG, "reply: ${msg.text}" + if (msg.ack.isNotEmpty()) " [ack=${msg.ack}]" else "")
                _ui.update { it.copy(reply = msg.text, done = it.done || msg.ack.isNotEmpty()) }
                val cbs = replyCallbacks.toList(); replyCallbacks.clear()
                cbs.forEach { it(msg.text.ifEmpty { "(${msg.ack})" }) }
            }
            is ServerMsg.SessionEnd -> onSessionEnd(msg)
            is ServerMsg.Earcon -> {
                earcons.play(msg.name)
                if (msg.name == "done") _ui.update { it.copy(done = true) }
            }
            is ServerMsg.Stop -> {
                player.stopAll()
                if (phase == Phase.SPEAKING) { setPhase(Phase.IDLE); focus.abandon() }
            }
            is ServerMsg.Listen -> if (!typedTurn && surface?.isVisible == true && phase !in setOf(Phase.LISTENING, Phase.CONNECTING)) activate("followup")
            is ServerMsg.Set -> msg.volume?.let { setVolume(it) }
            is ServerMsg.StreamOpen -> {
                keepAlive()
                if (msg.kind != "media") {
                    focus.request(exclusive = carMode)
                    if (phase !in setOf(Phase.LISTENING, Phase.CONNECTING)) { setPhase(Phase.SPEAKING); sendState("speaking") }
                }
            }
            is ServerMsg.Confirm -> {
                confirmEnds = 0
                _ui.update { it.copy(confirm = msg.summary.ifEmpty { msg.tool }) }
            }
            is ServerMsg.ToolCall -> runTool(msg)
            else -> {}
        }
    }

    private fun onSessionEnd(msg: ServerMsg.SessionEnd) {
        if (phase == Phase.LISTENING || phase == Phase.CONNECTING) return     // a new turn already started
        activeWake = 0
        if (_ui.value.confirm != null) { if (confirmEnds >= 1) clearConfirm() else confirmEnds++ }
        val seq = turnSeq
        player.whenSpeechIdle {
            if (seq != turnSeq) return@whenSpeechIdle
            if (msg.followUp && !typedTurn && surface?.isVisible == true) {
                main.postDelayed({ if (seq == turnSeq) activate("followup") }, 150)
            } else {
                if (phase != Phase.ERROR) setPhase(Phase.IDLE)
                sendState("idle")
                focus.abandon()
                val s = _ui.value
                scheduleDismiss(if (s.done && s.reply.isBlank()) 1800 else if (s.reply.length > 160) 15000 else 9000)
            }
        }
    }

    private fun runTool(call: ServerMsg.ToolCall) {
        Log.i(TAG, "tool_call ${call.name} ${call.args}")
        toolExec.execute {
            val result = try { tools.run(call.name, call.args) } catch (e: Exception) {
                Log.w(TAG, "tool ${call.name}", e)
                JSONObject().put("error", e.message ?: e.toString())
            }
            Log.i(TAG, "tool_result ${call.name} $result")
            connection.send(Proto.toolResult(call.callId, result))
        }
    }

    // ------------------------------------------------------------------ helpers for tools
    /** Starts an activity for a tool, from the main thread; false if nothing handles it. */
    fun launch(intent: Intent): Boolean = onMain {
        lastExternalLaunch = SystemClock.elapsedRealtime()
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            if (surface?.launch(intent) != true) app.startActivity(intent)
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (e: SecurityException) {
            Log.w(TAG, "launch: $e"); false
        }
    }

    fun navigateOnSurface(destination: String, mode: String): Boolean = onMain { surface?.navigate(destination, mode) == true }

    fun <T> onMain(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        var r: Result<T>? = null
        val latch = CountDownLatch(1)
        main.post { r = runCatching(block); latch.countDown() }
        latch.await(10, TimeUnit.SECONDS)
        return (r ?: Result.failure(IllegalStateException("main thread busy"))).getOrThrow()
    }

    private fun volumePercent(): Int? = try {
        val am = app.getSystemService(AudioManager::class.java)
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        if (max > 0) (am.getStreamVolume(AudioManager.STREAM_MUSIC) * 100f / max).roundToInt() else null
    } catch (_: Exception) { null }

    fun setVolume(percent: Int) {
        try {
            val am = app.getSystemService(AudioManager::class.java)
            val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
            am.setStreamVolume(AudioManager.STREAM_MUSIC, (percent.coerceIn(0, 100) * max / 100f).roundToInt(), 0)
        } catch (e: Exception) {
            Log.w(TAG, "volume: $e")
        }
    }

    companion object {
        const val KEEP_ALIVE_MS = 5 * 60_000L
        const val CONNECT_TIMEOUT_MS = 10_000L
    }
}
