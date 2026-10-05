package net.prototux.lapin.car

import android.content.Intent
import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.validation.HostValidator

/**
 * Android Auto (Car App Library, IoT category). Third-party apps can't take
 * the steering-wheel assistant button: Lapin is opened from the car's app
 * list and has a big "Talk" action.
 */
class LapinCarAppService : CarAppService() {
    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        else HostValidator.Builder(applicationContext)
            .addAllowedHosts(net.prototux.lapin.R.array.car_hosts_allowlist)
            .build()

    override fun onCreateSession(): Session = object : Session() {
        override fun onCreateScreen(intent: Intent): Screen = TalkScreen(carContext)
    }
}
