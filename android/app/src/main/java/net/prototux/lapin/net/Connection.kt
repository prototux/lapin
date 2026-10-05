package net.prototux.lapin.net

import android.os.Handler
import android.os.Looper
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import net.prototux.lapin.TAG
import net.prototux.lapin.protocol.ServerMsg
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.util.concurrent.TimeUnit

sealed interface ConnState {
    data object Disconnected : ConnState
    data object Connecting : ConnState
    data class Pending(val message: String) : ConnState
    data class Connected(val server: String) : ConnState
    data class Failed(val message: String) : ConnState
}

/**
 * The WebSocket to the server: hello, pairing, reconnection with backoff
 * (1, 2, 5, then 10 s). Text messages are delivered on the main thread,
 * audio frames on OkHttp's thread (straight to the player).
 */
class Connection(
    private val url: () -> String,
    private val hello: () -> JSONObject,
    private val onMessage: (ServerMsg) -> Unit,
    /** Called first, on the socket thread, in order with the audio frames (stream control). */
    private val onWire: (ServerMsg) -> Unit,
    private val onAudio: (ByteArray) -> Unit,
    private val onDown: () -> Unit,
) {
    private val main = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow<ConnState>(ConnState.Disconnected)
    val state: StateFlow<ConnState> = _state

    @Volatile private var ws: WebSocket? = null
    @Volatile private var ready = false
    private var wanted = false
    private var fatal = false
    private var attempt = 0
    private val reconnect = Runnable { if (wanted && ws == null) open() }

    val isReady: Boolean get() = ready

    /** Connects (if not already connected or connecting) and keeps reconnecting until [close]. */
    fun connect() {
        main.removeCallbacks(reconnect)
        wanted = true
        fatal = false
        if (ws == null) open()
    }

    fun close() {
        wanted = false
        main.removeCallbacks(reconnect)
        ws?.close(1000, "idle")
        ws = null
        ready = false
        _state.value = ConnState.Disconnected
    }

    /** Settings changed: start over. */
    fun restart() {
        val was = wanted
        close()
        attempt = 0
        if (was) connect()
    }

    fun send(msg: JSONObject): Boolean {
        val w = ws ?: return false
        if (!ready) return false
        return w.send(msg.toString())
    }

    fun sendBinary(b: ByteArray): Boolean {
        val w = ws ?: return false
        if (!ready) return false
        return w.send(b.toByteString())
    }

    private fun open() {
        val u = url()
        val req = try { Request.Builder().url(u.replaceFirst(Regex("^ws", RegexOption.IGNORE_CASE), "http")).build() }
        catch (e: IllegalArgumentException) {
            fatal = true
            _state.value = ConnState.Failed("bad server URL: $u")
            onDown()
            return
        }
        _state.value = ConnState.Connecting
        Log.i(TAG, "connecting to $u")
        ws = client.newWebSocket(req, Listener())
    }

    private inner class Listener : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            webSocket.send(hello().toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val msg = ServerMsg.parse(text) ?: return
            if (webSocket === ws) onWire(msg)
            main.post {
                if (webSocket !== ws) return@post
                when (msg) {
                    is ServerMsg.Welcome -> { ready = true; attempt = 0; _state.value = ConnState.Connected(msg.server) }
                    is ServerMsg.Pending -> _state.value = ConnState.Pending(msg.message)
                    is ServerMsg.Error -> if (!ready) { fatal = true; _state.value = ConnState.Failed(msg.message) }
                    else -> {}
                }
                onMessage(msg)
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            if (webSocket === ws) onAudio(bytes.toByteArray())
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            down(webSocket, null)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = down(webSocket, null)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
            down(webSocket, t.message ?: t.javaClass.simpleName)

        private fun down(webSocket: WebSocket, error: String?) {
            main.post {
                if (webSocket !== ws) return@post
                ws = null
                ready = false
                if (error != null) Log.w(TAG, "connection lost: $error")
                if (!fatal) _state.value = if (error != null) ConnState.Failed(error) else ConnState.Disconnected
                onDown()
                if (wanted && !fatal) {
                    val delay = BACKOFF[attempt.coerceAtMost(BACKOFF.size - 1)]
                    attempt++
                    main.postDelayed(reconnect, delay)
                }
            }
        }
    }

    companion object {
        private val BACKOFF = longArrayOf(1000, 2000, 5000, 10000)
    }
}
