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
 * to the chosen assistant (AARE) open it with ACTION_VOICE_COMMAND.
 */
class LapinCarAppService : CarAppService() {
    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        else HostValidator.Builder(applicationContext)
            .addAllowedHosts(net.prototux.lapin.R.array.car_hosts_allowlist)
            .build()

    override fun onCreateSession(): Session = object : Session() {
        private lateinit var screen: TalkScreen

        override fun onCreateScreen(intent: Intent): Screen =
            TalkScreen(carContext, listenOnStart = intent.action == Intent.ACTION_VOICE_COMMAND).also { screen = it }

        // Hosts that hand the voice button to other apps (AARE) send ACTION_VOICE_COMMAND when
        // it's pressed: start listening, as if Talk was tapped.
        override fun onNewIntent(intent: Intent) {
            if (intent.action == Intent.ACTION_VOICE_COMMAND && ::screen.isInitialized) screen.talk()
        }
    }
}
