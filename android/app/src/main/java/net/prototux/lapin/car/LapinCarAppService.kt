package net.prototux.lapin.car

import android.content.Intent
import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.validation.HostValidator

/**
 * Android Auto (Car App Library, IoT category). Google's Android Auto keeps
 * the steering-wheel assistant button for itself: there Lapin is opened from
 * the car's app list and has a big "Talk" action. Hosts that hand the button
 * to the chosen assistant (AARE) open it with ACTION_VOICE_COMMAND: there the
 * button is the only control, without Talk and Stop actions.
 */
class LapinCarAppService : CarAppService() {
    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        else HostValidator.Builder(applicationContext)
            .addAllowedHosts(net.prototux.lapin.R.array.car_hosts_allowlist)
            .build()

    override fun onCreateSession(): Session = object : Session() {
        private lateinit var screen: TalkScreen

        override fun onCreateScreen(intent: Intent): Screen {
            val voice = intent.action == Intent.ACTION_VOICE_COMMAND
            // AARE always hands Lapin the button, even when it was opened from the car's app list
            val voiceButton = voice || carContext.hostInfo?.packageName == AARE
            return TalkScreen(carContext, voiceButton, listenOnStart = voice).also { screen = it }
        }

        // Each press of the voice button: listen, or stop what Lapin is doing.
        override fun onNewIntent(intent: Intent) {
            if (intent.action == Intent.ACTION_VOICE_COMMAND && ::screen.isInitialized) screen.voiceButton()
        }
    }

    private companion object {
        const val AARE = "net.prototux.aare"
    }
}
