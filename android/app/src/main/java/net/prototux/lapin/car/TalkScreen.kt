package net.prototux.lapin.car

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.car.app.CarContext
import androidx.core.net.toUri
import androidx.car.app.CarToast
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.CarColor
import androidx.car.app.model.Header
import androidx.car.app.model.MessageTemplate
import androidx.car.app.model.Template
import androidx.car.app.versioning.CarAppApiLevels
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import net.prototux.lapin.LapinApp
import net.prototux.lapin.R
import net.prototux.lapin.core.Phase
import net.prototux.lapin.core.Surface

/** The car screen: what was heard, the answer, and Talk / Stop (or Yes / No for a confirmation). */
class TalkScreen(carContext: CarContext) : Screen(carContext), Surface {
    private val assistant get() = LapinApp.assistant
    private var started = false
    override val isVisible: Boolean get() = started

    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onCreate(owner: LifecycleOwner) {
                if (carContext.carAppApiLevel >= CarAppApiLevels.LEVEL_5) assistant.enterCar { CarMic(carContext) }
            }
            override fun onStart(owner: LifecycleOwner) { started = true; assistant.attach(this@TalkScreen) }
            override fun onStop(owner: LifecycleOwner) { started = false; assistant.detach(this@TalkScreen) }
            override fun onDestroy(owner: LifecycleOwner) { assistant.dismissByUser(); assistant.exitCar() }
        })
        // refresh on meaningful changes only (templates have a refresh quota): not on every partial transcript
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                assistant.ui.map { Triple(it.phase, it.reply + "|" + it.hint + "|" + (it.confirm ?: ""), if (it.transcriptFinal) it.transcript else "") }
                    .distinctUntilChanged()
                    .collect { invalidate() }
            }
        }
    }

    override fun dismiss() {}       // the car screen stays

    override fun navigate(destination: String, mode: String): Boolean = try {
        carContext.startCarApp(Intent(CarContext.ACTION_NAVIGATE, ("geo:0,0?q=" + Uri.encode(destination)).toUri()))
        true
    } catch (e: Exception) {
        false
    }

    private fun talk() {
        if (carContext.carAppApiLevel < CarAppApiLevels.LEVEL_5) {
            CarToast.makeText(carContext, R.string.car_needs_api, CarToast.LENGTH_LONG).show(); return
        }
        if (carContext.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            carContext.requestPermissions(listOf(Manifest.permission.RECORD_AUDIO)) { granted, _ ->
                if (granted.isNotEmpty()) assistant.orbTap()
                else CarToast.makeText(carContext, R.string.car_needs_mic, CarToast.LENGTH_LONG).show()
            }
            return
        }
        assistant.orbTap()
    }

    override fun onGetTemplate(): Template {
        val s = assistant.ui.value
        val c = carContext
        val title = when (s.phase) {
            Phase.LISTENING -> c.getString(R.string.state_listening)
            Phase.THINKING -> c.getString(R.string.state_thinking)
            Phase.SPEAKING -> c.getString(R.string.state_speaking)
            Phase.CONNECTING -> c.getString(R.string.state_connecting)
            Phase.PENDING -> c.getString(R.string.state_pending)
            Phase.ERROR -> c.getString(R.string.state_error)
            Phase.IDLE -> c.getString(R.string.app_name)
        }
        val body = buildString {
            if (s.transcript.isNotBlank()) append(c.getString(R.string.car_heard, s.transcript)).append("\n\n")
            if (s.reply.isNotBlank()) append(s.reply)
            if (s.confirm != null) append("\n\n").append(s.confirm.replaceFirstChar { it.uppercase() }).append(" ?")
            if (s.hint.isNotBlank()) append("\n").append(s.hint)
        }.trim().ifEmpty {
            if (c.carAppApiLevel < CarAppApiLevels.LEVEL_5) c.getString(R.string.car_needs_api) else c.getString(R.string.car_ready)
        }
        val header = Header.Builder().setTitle(title).setStartHeaderAction(Action.APP_ICON).build()
        val b = MessageTemplate.Builder(body.take(900)).setHeader(header)
        if (s.phase == Phase.THINKING || s.phase == Phase.CONNECTING) {
            return MessageTemplate.Builder(c.getString(R.string.state_thinking)).setHeader(header).setLoading(true).build()
        }
        if (s.confirm != null) {
            b.addAction(Action.Builder().setTitle(c.getString(R.string.yes)).setBackgroundColor(CarColor.GREEN)
                .setOnClickListener { assistant.confirm(true) }.build())
            b.addAction(Action.Builder().setTitle(c.getString(R.string.no))
                .setOnClickListener { assistant.confirm(false) }.build())
        } else {
            b.addAction(Action.Builder().setTitle(c.getString(R.string.car_talk)).setBackgroundColor(CarColor.BLUE)
                .setOnClickListener { talk() }.build())
            if (s.phase == Phase.LISTENING || s.phase == Phase.SPEAKING)
                b.addAction(Action.Builder().setTitle(c.getString(R.string.car_stop))
                    .setOnClickListener { assistant.dismissByUser() }.build())
        }
        return b.build()
    }
}
