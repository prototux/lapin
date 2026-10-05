package net.prototux.lapin

import android.app.Application
import net.prototux.lapin.core.Assistant

class LapinApp : Application() {
    lateinit var assistant: Assistant
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        assistant = Assistant(this)
    }

    companion object {
        lateinit var instance: LapinApp
            private set
        val assistant: Assistant get() = instance.assistant
    }
}

const val TAG = "Lapin"
