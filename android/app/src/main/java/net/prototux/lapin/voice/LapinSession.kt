package net.prototux.lapin.voice

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.os.SystemClock
import android.service.voice.VoiceInteractionSession
import android.view.View
import android.view.WindowManager
import androidx.compose.ui.platform.ComposeView
import androidx.core.view.WindowCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import net.prototux.lapin.LapinApp
import net.prototux.lapin.core.Surface
import net.prototux.lapin.ui.AssistantOverlay
import net.prototux.lapin.ui.LapinTheme

/**
 * The system assistant session: the bottom card over the current app.
 * Implements the lifecycle owners Compose needs outside an activity.
 */
class LapinSession(context: Context) : VoiceInteractionSession(context),
    LifecycleOwner, SavedStateRegistryOwner, ViewModelStoreOwner, Surface {

    private val assistant get() = LapinApp.assistant
    private val registry = LifecycleRegistry(this)
    private val savedState = SavedStateRegistryController.create(this)
    private val store = ViewModelStore()
    private var shown = false
    private var hidingOurselves = false

    override val lifecycle: Lifecycle get() = registry
    override val savedStateRegistry: SavedStateRegistry get() = savedState.savedStateRegistry
    override val viewModelStore: ViewModelStore get() = store
    override val isVisible: Boolean get() = shown

    override fun onCreate() {
        super.onCreate()
        savedState.performRestore(null)
        registry.currentState = Lifecycle.State.CREATED
    }

    override fun onCreateContentView(): View {
        window.window?.let { w ->
            WindowCompat.setDecorFitsSystemWindows(w, false)
            @Suppress("DEPRECATION")
            w.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            w.decorView.setViewTreeLifecycleOwner(this)
            w.decorView.setViewTreeSavedStateRegistryOwner(this)
            w.decorView.setViewTreeViewModelStoreOwner(this)
        }
        return ComposeView(context).apply {
            setViewTreeLifecycleOwner(this@LapinSession)
            setViewTreeSavedStateRegistryOwner(this@LapinSession)
            setViewTreeViewModelStoreOwner(this@LapinSession)
            setContent { LapinTheme { AssistantOverlay(assistant, onDismiss = ::userDismiss) } }
        }
    }

    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        shown = true
        hidingOurselves = false
        registry.currentState = Lifecycle.State.RESUMED
        assistant.attach(this)
        assistant.activate("button")
    }

    override fun onHide() {
        super.onHide()
        shown = false
        if (registry.currentState.isAtLeast(Lifecycle.State.STARTED)) registry.currentState = Lifecycle.State.CREATED
        assistant.detach(this)
        // a tool opened another app, or we closed after an answer: not a dismissal
        val external = SystemClock.elapsedRealtime() - assistant.lastExternalLaunch < 4000
        if (!hidingOurselves && !external) assistant.dismissByUser()
        hidingOurselves = false
    }

    override fun onDestroy() {
        registry.currentState = Lifecycle.State.DESTROYED
        store.clear()
        super.onDestroy()
    }

    override fun onBackPressed() = userDismiss()

    /** Auto-dismiss after an answer. */
    override fun dismiss() {
        hidingOurselves = true
        hide()
    }

    override fun launch(intent: Intent): Boolean {
        context.startActivity(intent)
        // the opened app takes the screen: fold the card (speech goes on)
        hidingOurselves = true
        hide()
        return true
    }

    private fun userDismiss() {
        assistant.dismissByUser()
        hidingOurselves = true
        hide()
    }
}
