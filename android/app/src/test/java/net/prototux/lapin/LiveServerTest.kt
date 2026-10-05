package net.prototux.lapin

import net.prototux.lapin.protocol.Proto
import net.prototux.lapin.protocol.ServerMsg
import net.prototux.lapin.tools.ToolSpecs
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.BeforeClass
import org.junit.FixMethodOrder
import org.junit.Test
import org.junit.runners.MethodSorters
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * End-to-end protocol test against a running server, with the app's own codec
 * (Proto / ServerMsg) and tool declarations. Opt-in:
 *
 *   LAPIN_LIVE_WS=ws://localhost:8765/v1/device LAPIN_LIVE_ADMIN=http://localhost:8090 ./gradlew testDebugUnitTest --tests '*LiveServerTest*'
 *
 * Registers a "test-" device, approves it through the admin API, and deletes it afterwards.
 */
@FixMethodOrder(MethodSorters.NAME_ASCENDING)
class LiveServerTest {
    sealed interface Ev {
        data class Msg(val m: ServerMsg, val raw: String) : Ev
        data class Audio(val bytes: ByteArray) : Ev
        data class Closed(val why: String) : Ev
    }

    class Client(url: String, hello: JSONObject) {
        val events = LinkedBlockingQueue<Ev>()
        val ws: WebSocket = http.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { webSocket.send(hello.toString()) }
            override fun onMessage(webSocket: WebSocket, text: String) { ServerMsg.parse(text)?.let { events.put(Ev.Msg(it, text)) } }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) { events.put(Ev.Audio(bytes.toByteArray())) }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { events.put(Ev.Closed("closed $code $reason")) }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { events.put(Ev.Closed("failure $t")) }
        })

        fun send(j: JSONObject) = assertTrue(ws.send(j.toString()))

        /** Next server message of type [T] (skipping others), collecting everything seen. */
        inline fun <reified T : ServerMsg> await(seen: MutableList<Ev>? = null, timeoutS: Long = 60, onEach: (Ev) -> Unit = {}): T {
            val deadline = System.currentTimeMillis() + timeoutS * 1000
            while (true) {
                val left = deadline - System.currentTimeMillis()
                val ev = events.poll(maxOf(1, left), TimeUnit.MILLISECONDS) ?: throw AssertionError("timeout waiting for ${T::class.simpleName}; saw $seen")
                seen?.add(ev)
                onEach(ev)
                if (ev is Ev.Closed) throw AssertionError("socket ${ev.why} while waiting for ${T::class.simpleName}")
                if (ev is Ev.Msg && ev.m is T) return ev.m
            }
        }
    }

    @Test fun a_pairing() {
        val c = Client(ws!!, hello())
        val first = c.await<ServerMsg>(timeoutS = 10)
        if (first is ServerMsg.Pending) {
            println("pending: ${first.message}")
            admin("POST", "/api/devices/$deviceId/approve", """{"approved": true}""")
        }
        val w = if (first is ServerMsg.Welcome) first else c.await<ServerMsg.Welcome>(timeoutS = 15)
        println("welcome from ${w.server}, name ${w.name}")
        client = c
    }

    @Test fun b_typedRequestSpoken() {
        val c = client ?: return
        val seen = mutableListOf<Ev>()
        c.send(Proto.text("Dis juste bonjour.", true))
        val reply = c.await<ServerMsg.Reply>(seen, 90)
        println("reply: ${reply.text}")
        c.await<ServerMsg.SessionEnd>(seen, 60)
        assertTrue(reply.text.isNotBlank())
        val opens = seen.mapNotNull { (it as? Ev.Msg)?.m as? ServerMsg.StreamOpen }
        assertTrue("a tts stream was opened: $seen", opens.isNotEmpty())
        val open = opens.first()
        val frames = seen.filterIsInstance<Ev.Audio>().mapNotNull { Proto.parsePlayback(it.bytes) }
        assertTrue("audio frames received", frames.isNotEmpty())
        assertTrue("frames carry the stream id", frames.all { f -> opens.any { it.id == f.streamId } })
        val bytes = frames.filter { it.streamId == open.id }.sumOf { it.length }
        println("tts stream ${open.id}: ${open.rate} Hz x${open.channels}, ${bytes} bytes = ${bytes * 1000L / (open.rate * 2 * open.channels)} ms")
        assertTrue(seen.any { (it as? Ev.Msg)?.m == ServerMsg.StreamClose(open.id, true) })
    }

    /** Plain device tools. The local LLM doesn't always call them: report the rate, require most to work. */
    @Test fun c_deviceToolCall() {
        client ?: return
        val cases = listOf(
            "Ouvre les paramètres" to "open_app", "Lance l'appareil photo" to "open_app",
            "Allume la lampe torche" to "flashlight", "Quel est le niveau de batterie du téléphone ?" to "battery_status",
            "Mets un minuteur de 5 minutes sur mon téléphone" to "set_phone_timer",
        )
        var good = 0
        for ((phrase, tool) in cases) {
            // a fresh device (= fresh conversation) per phrase: one refusal in the history makes the model refuse again
            val c = freshClient()
            val seen = mutableListOf<Ev>()
            c.send(Proto.text(phrase, false))
            val calls = mutableListOf<ServerMsg.ToolCall>()
            val reply = c.await<ServerMsg.Reply>(seen, 90) { ev ->
                val m = (ev as? Ev.Msg)?.m
                if (m is ServerMsg.ToolCall) {
                    calls += m
                    val r = when (m.name) {
                        "battery_status" -> JSONObject().put("battery", 64).put("charging", false)
                        "phone_play_music" -> JSONObject().put("error", "\"${m.args.optString("query")}\" is an app on the phone, not music: use open_app")
                        "open_app" -> JSONObject().put("ok", true).put("opened", m.args.optString("app"))
                        else -> JSONObject().put("ok", true)
                    }
                    c.send(Proto.toolResult(m.callId, r))
                }
            }
            c.await<ServerMsg.SessionEnd>(seen, 30)
            val earcons = seen.mapNotNull { ((it as? Ev.Msg)?.m as? ServerMsg.Earcon)?.name }
            val ok = calls.any { it.name == tool }
            if (ok) good++
            c.ws.close(1000, "done")
            println("[${if (ok) "OK" else "--"}] '$phrase' -> ${calls.map { "${it.name}${it.args}" }} reply='${reply.text.take(100)}' ack='${reply.ack}' earcons=$earcons")
        }
        println("device tools called: $good/${cases.size}")
        assertTrue("most device tool requests should call the tool ($good/${cases.size})", good >= cases.size - 1)
    }

    /** Music on the phone's own player: the server's fast paths call the device tools by name. */
    @Test fun f_phoneMusic() {
        client ?: return
        val cases = listOf(
            Triple("Mets du Daft Punk", "phone_play_music", JSONObject("""{"ok":true,"playing":"songs by Daft Punk","app":"Tempus"}""")),
            Triple("Chanson suivante", "phone_media", JSONObject("""{"ok":true,"action":"next","app":"Tempus"}""")),
            Triple("C'est quoi cette musique ?", "phone_now_playing",
                JSONObject("""{"title":"One More Time","artist":"Daft Punk","album":"Discovery","app":"Tempus","playing":true}""")),
        )
        val failures = mutableListOf<String>()
        for ((phrase, tool, result) in cases) {
            val c = freshClient()
            val seen = mutableListOf<Ev>()
            c.send(Proto.text(phrase, false))
            val calls = mutableListOf<ServerMsg.ToolCall>()
            val reply = c.await<ServerMsg.Reply>(seen, 60) { ev ->
                val m = (ev as? Ev.Msg)?.m
                if (m is ServerMsg.ToolCall) { calls += m; c.send(Proto.toolResult(m.callId, if (m.name == tool) result else JSONObject().put("ok", true))) }
            }
            val end = c.await<ServerMsg.SessionEnd>(seen, 30)
            c.ws.close(1000, "done")
            val earcons = seen.mapNotNull { ((it as? Ev.Msg)?.m as? ServerMsg.Earcon)?.name }
            println("'$phrase' -> ${calls.map { "${it.name}${it.args}" }} reply='${reply.text}' ack='${reply.ack}' earcons=$earcons follow_up=${end.followUp}")
            val call = calls.firstOrNull { it.name == tool }
            if (call == null) { failures += "$phrase: no $tool call"; continue }
            when (tool) {
                "phone_play_music" -> {
                    if (!call.args.optString("query").contains("daft", ignoreCase = true)) failures += "$phrase: query ${call.args}"
                    if (call.args.optString("kind") !in MusicKinds) failures += "$phrase: kind ${call.args}"
                    if (reply.ack != "done" && "done" !in earcons) failures += "$phrase: expected the done chime (silent tool)"
                }
                "phone_media" -> if (call.args.optString("action") != "next") failures += "$phrase: action ${call.args}"
                "phone_now_playing" -> if (!reply.text.contains("One More Time")) failures += "$phrase: reply doesn't name the track"
            }
        }
        assertTrue(failures.joinToString("; "), failures.isEmpty())
    }

    private val MusicKinds = setOf("auto", "artist", "album", "playlist", "song", "genre", "random")

    private fun freshClient(): Client {
        val id = "test-phone-" + UUID.randomUUID().toString().take(8)
        extraIds += id
        val c = Client(ws!!, Proto.hello(id, token, "phone", "Lapin JVM test", "jason", "test", ToolSpecs.toJson(ToolSpecs.phone(true)), 50))
        if (c.await<ServerMsg>(timeoutS = 10) is ServerMsg.Pending) {
            admin("POST", "/api/devices/$id/approve", """{"approved": true}""")
            c.await<ServerMsg.Welcome>(timeoutS = 15)
        }
        return c
    }

    @Test fun d_confirmationFlow() {
        val c = client ?: return
        val seen = mutableListOf<Ev>()
        c.send(Proto.text("Envoie un SMS à Marie Dupont pour lui dire que j'arrive", false))
        var calls = 0
        val respond = { ev: Ev ->
            val m = (ev as? Ev.Msg)?.m
            if (m is ServerMsg.ToolCall) {
                calls++
                println("tool_call ${m.name} ${m.args}")
                val r = if (m.name == "find_contact") JSONObject("""{"contacts":[{"name":"Marie Dupont","numbers":["+33600000000 (mobile)"]}]}""")
                else JSONObject().put("ok", true).put("sent_to", "Marie Dupont")
                c.send(Proto.toolResult(m.callId, r))
            }
        }
        val reply = c.await<ServerMsg.Reply>(seen, 90, respond)
        c.await<ServerMsg.SessionEnd>(seen, 30, respond)
        val confirm = seen.mapNotNull { (it as? Ev.Msg)?.m as? ServerMsg.Confirm }.firstOrNull()
        println("question: ${reply.text} | confirm: $confirm")
        assertNotNull("a confirm message for the Yes/No buttons; saw $seen", confirm)
        assertTrue(confirm!!.tool == "send_phone_message")
        // the "Yes" button
        val seen2 = mutableListOf<Ev>()
        var sent: ServerMsg.ToolCall? = null
        c.send(Proto.confirmReply(true))
        val reply2 = c.await<ServerMsg.Reply>(seen2, 90) { ev ->
            val m = (ev as? Ev.Msg)?.m
            if (m is ServerMsg.ToolCall && m.name == "send_phone_message") sent = m
            respond(ev)
        }
        c.await<ServerMsg.SessionEnd>(seen2, 30)
        println("after yes: ${reply2.text} (tool: ${sent?.args})")
        assertNotNull("the confirmed tool came back as a tool_call; saw $seen2", sent)
    }

    @Test fun e_voiceTurnWithSilence() {
        val c = client ?: return
        val seen = mutableListOf<Ev>()
        c.send(Proto.wake(1, "button"))
        // 12 s of silence as 20 ms 0x01 frames, real time (the server gives up after 9 s of audio without speech)
        val silence = ByteArray(Proto.MIC_FRAME_BYTES)
        val sender = Thread {
            repeat(600) {
                if (!c.ws.send(Proto.micFrame(silence).toByteString())) return@Thread
                Thread.sleep(20)
            }
        }.apply { start() }
        val end = c.await<ServerMsg>(seen, 40) { }
        val result = if (end is ServerMsg.Cancel || end is ServerMsg.Eot) end else c.await<ServerMsg.Cancel>(seen, 40)
        sender.interrupt()
        println("silence turn: ${seen.mapNotNull { (it as? Ev.Msg)?.raw }}")
        assertTrue("wake_ack then cancel/eot: $seen", result is ServerMsg.Cancel || result is ServerMsg.Eot)
        if (result is ServerMsg.Cancel) assertEquals(1, result.wakeId)
    }

    companion object {
        val http: OkHttpClient = OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS).build()
        private var ws: String? = null
        private var adminUrl: String? = null
        val deviceId = "test-phone-" + UUID.randomUUID().toString().take(8)
        private val token = UUID.randomUUID().toString() + UUID.randomUUID().toString()
        var client: Client? = null
        val extraIds = mutableListOf<String>()

        @BeforeClass @JvmStatic fun setup() {
            ws = System.getenv("LAPIN_LIVE_WS")
            adminUrl = System.getenv("LAPIN_LIVE_ADMIN") ?: "http://localhost:8090"
            assumeTrue("set LAPIN_LIVE_WS to run against a live server", !ws.isNullOrEmpty())
        }

        @AfterClass @JvmStatic fun cleanup() {
            if (ws.isNullOrEmpty()) return
            client?.ws?.close(1000, "done")
            Thread.sleep(300)
            for (id in listOf(deviceId) + extraIds)
                runCatching { admin("DELETE", "/api/devices/$id", null) }.onFailure { println("cleanup failed: $it") }
            println("deleted $deviceId")
        }

        fun hello(): JSONObject = Proto.hello(deviceId, token, "phone", "Lapin JVM test", "jason", "test",
            ToolSpecs.toJson(ToolSpecs.phone(true)), 50)

        fun admin(method: String, path: String, body: String?): String {
            val rb = body?.toRequestBody("application/json".toMediaType())
            val req = Request.Builder().url(adminUrl + path).method(method, rb).build()
            http.newCall(req).execute().use { r ->
                val t = r.body?.string() ?: ""
                println("$method $path -> ${r.code} ${t.take(200)}")
                assertTrue("$method $path: ${r.code}", r.isSuccessful)
                return t
            }
        }
    }
}
