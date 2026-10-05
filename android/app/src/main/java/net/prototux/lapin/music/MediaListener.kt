package net.prototux.lapin.music

import android.content.ComponentName
import android.content.Context
import android.service.notification.NotificationListenerService
import androidx.core.app.NotificationManagerCompat

/**
 * Optional notification access: lets Lapin see every app's media session
 * (what's playing, and control the app actually playing). Lapin reads no
 * notifications.
 */
class MediaListener : NotificationListenerService() {
    companion object {
        fun component(context: Context) = ComponentName(context, MediaListener::class.java)
        fun granted(context: Context) = context.packageName in NotificationManagerCompat.getEnabledListenerPackages(context)
    }
}
