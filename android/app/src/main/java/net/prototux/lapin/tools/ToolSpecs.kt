package net.prototux.lapin.tools

import org.json.JSONArray
import org.json.JSONObject

/**
 * Declarations of the device tools sent in `hello.tools` (PROTOCOL.md §5).
 * The server appends "(on the user's phone)" / "(on the TV)" to each description.
 *
 * Names avoid the server's own tools (server tools with the same name win):
 * the server already has send_message, set_alarm, set_timer, media_control...
 */
object ToolSpecs {
    val NAME = Regex("[a-z][a-z0-9_]{1,40}")

    class P(val name: String, val type: String, val description: String, val enum: List<String>? = null)

    private fun tool(
        name: String, description: String, params: List<P> = emptyList(), required: List<String> = emptyList(),
        confirm: Boolean = false, silent: Boolean = false, summary: String = "",
    ): JSONObject {
        return tool(name, description, params, required, confirm, silent, summary, emptyList())
    }

    private fun tool(
        name: String, description: String, params: List<P>, required: List<String>,
        confirm: Boolean, silent: Boolean, summary: String, replaces: List<String>,
    ): JSONObject {
        val props = JSONObject()
        for (p in params) {
            val o = JSONObject().put("type", p.type).put("description", p.description)
            p.enum?.let { o.put("enum", JSONArray(it)) }
            props.put(p.name, o)
        }
        return JSONObject()
            .put("name", name)
            .put("description", description)
            .put("parameters", JSONObject().put("type", "object").put("properties", props).put("required", JSONArray(required)))
            .put("confirm", confirm)
            .put("silent", silent)
            .apply { if (summary.isNotEmpty()) put("summary", summary) }
            .apply { if (replaces.isNotEmpty()) put("replaces", JSONArray(replaces)) }
    }

    fun phone(fr: Boolean): List<JSONObject> = listOf(
        tool(
            "send_phone_message",
            "Send a text message from the user's phone to one of their phone contacts or to a phone number. " +
                "app \"sms\" sends it directly; \"signal\" opens Signal with the message ready, the user taps send. " +
                "Use sms unless the user asks for Signal. For household members on Telegram/web chat use send_message instead.",
            listOf(
                P("contact", "string", "contact name as the user said it (looked up in the phone's address book), or a phone number"),
                P("message", "string", "the message text, as the user wants it sent"),
                P("app", "string", "sms or signal", listOf("sms", "signal")),
            ),
            listOf("contact", "message", "app"), confirm = true,
            summary = if (fr) "envoyer « {message} » à {contact} ({app})" else "send “{message}” to {contact} ({app})",
        ),
        tool(
            "call_contact",
            "Place a phone call from the user's phone to a contact or a phone number.",
            listOf(P("contact", "string", "contact name as the user said it (looked up in the phone's address book), or a phone number")),
            listOf("contact"), confirm = true,
            summary = if (fr) "appeler {contact}" else "call {contact}",
        ),
        tool(
            "find_contact",
            "Look up a person in the phone's address book (fuzzy match on the name). Returns the matching contacts " +
                "with their phone numbers. Use for \"what is X's number\" or to check who the user means.",
            listOf(P("name", "string", "the name to look up")), listOf("name"),
        ),
        tool(
            "start_navigation",
            "Start turn-by-turn navigation to a destination in the phone's maps app (Google Maps, Waze, Organic Maps...). " +
                "The destination can be an address, a place name or a search like \"nearest gas station\".",
            listOf(
                P("destination", "string", "where to go"),
                P("mode", "string", "travel mode, default driving", listOf("driving", "walking", "bicycling", "transit")),
            ),
            listOf("destination"),
        ),
        tool(
            "open_app",
            "Open an app on the phone the user is talking to, by its name. Use it whenever the user says " +
                "\"open/launch X\" / \"ouvre/lance X\" for an app or a phone screen: Settings/Paramètres, Camera/Appareil photo, " +
                "Spotify, Maps, WhatsApp... You CAN do this: the phone opens it.",
            listOf(P("app", "string", "the app name as the user said it")), listOf("app"), silent = true,
        ),
        tool(
            "set_phone_alarm",
            "Set an alarm in the phone's own clock app (it rings on the phone, even offline). " +
                "Prefer it to set_alarm for alarms asked from the phone.",
            listOf(
                P("hour", "integer", "hour, 0-23"),
                P("minute", "integer", "minute, 0-59"),
                P("label", "string", "optional label"),
            ),
            listOf("hour", "minute"),
        ),
        tool(
            "set_phone_timer",
            "Start a countdown timer in the phone's own clock app (it rings on the phone). " +
                "Prefer it to set_timer for timers asked from the phone.",
            listOf(P("seconds", "integer", "duration in seconds"), P("label", "string", "optional label")),
            listOf("seconds"),
        ),
        tool(
            "flashlight",
            "Turn the phone's flashlight (torch, \"lampe torche\") on or off. You CAN do this: the phone does it.",
            listOf(P("on", "boolean", "true to turn it on, false to turn it off")), listOf("on"), silent = true,
        ),
        phonePlayMusic(),
        phoneMedia("the phone", "Spotify, Tempus, YouTube, podcasts..."),
        phoneNowPlaying("the phone"),
        tool(
            "get_location",
            "Get the phone's current location: coordinates and street address. Use for \"where am I\" " +
                "or before searching for places near the user.",
        ),
        tool("battery_status", "Get the phone's battery level and whether it is charging."),
        tool(
            "open_url",
            "Open a web page in the phone's browser.",
            listOf(P("url", "string", "the address, e.g. https://example.com")), listOf("url"),
        ),
        tool(
            "create_calendar_event",
            "Prepare an event in the phone's calendar app: it opens filled in and the user taps save.",
            listOf(
                P("title", "string", "event title"),
                P("start_iso", "string", "start, ISO 8601 local time, e.g. 2026-10-05T14:00 (a date alone = all day)"),
                P("end_iso", "string", "end, ISO 8601 local time (default: one hour after the start)"),
                P("location", "string", "optional place"),
            ),
            listOf("title", "start_iso"),
        ),
        tool(
            "do_not_disturb",
            "Turn the phone's Do Not Disturb mode on or off.",
            listOf(P("on", "boolean", "true to turn it on, false to turn it off")), listOf("on"),
        ),
    )

    fun tv(): List<JSONObject> = listOf(
        tool(
            "open_app",
            "Open an app on the TV by its name (e.g. YouTube, Netflix, Plex, Kodi, Settings).",
            listOf(P("app", "string", "the app name as the user said it")), listOf("app"), silent = true,
        ),
        phoneMedia("the TV", "YouTube, Plex, Kodi, Netflix..."),
        phoneNowPlaying("the TV"),
        tool(
            "open_url",
            "Open a web page or a video link on the TV.",
            listOf(P("url", "string", "the address, e.g. https://youtube.com/...")), listOf("url"),
        ),
    )

    /** Music in the phone's own player app; replaces the server's streaming tools for this device. */
    private fun phonePlayMusic() = tool(
        "phone_play_music",
        "Play music in the user's music app on the phone (their Subsonic/Navidrome library): an artist, album, " +
            "playlist, song or genre, or random music. Use it for any \"play / mets / joue / lance <music>\" request " +
            "made from the phone.",
        listOf(
            P("query", "string", "what to play, as the user said it (artist, album, song, playlist or genre name); empty for random"),
            P("kind", "string", "what the query is, auto if unsure", MusicKinds),
        ),
        listOf("query"), confirm = false, silent = true, summary = "",
        replaces = listOf("subsonic_play", "jellyfin_play_music"),
    )

    /** The protocol's special name for the fast paths ("suivante", "pause"). */
    private fun phoneMedia(where: String, examples: String) = tool(
        "phone_media",
        "Control the music or video playing in an app on $where ($examples): play, pause, next or previous track. " +
            "Not for the music on the home speakers.",
        listOf(P("action", "string", "what to do", listOf("play", "pause", "next", "previous"))),
        listOf("action"), silent = true,
    )

    /** The protocol's special name for "c'est quoi cette musique ?". */
    private fun phoneNowPlaying(where: String) = tool(
        "phone_now_playing",
        "What is playing in the music/video app on $where: title, artist, album, app, and whether it is playing.",
    )

    private val MusicKinds = listOf("auto", "artist", "album", "playlist", "song", "genre", "random")

    fun toJson(tools: List<JSONObject>): JSONArray = JSONArray().apply { tools.forEach { put(it) } }
}
