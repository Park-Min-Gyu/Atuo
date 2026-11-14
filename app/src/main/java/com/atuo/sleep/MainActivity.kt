package com.atuo.sleep

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.mutableIntStateOf
import com.atuo.sleep.data.EventStore
import com.atuo.sleep.data.Permissions
import com.atuo.sleep.data.Prefs
import com.atuo.sleep.track.TrackingService
import com.atuo.sleep.ui.ScreenActions
import com.atuo.sleep.ui.SleepScreen

class MainActivity : ComponentActivity() {
    /** 값이 바뀌면 화면이 권한 상태와 분석 결과를 다시 읽는다. */
    private val refresh = mutableIntStateOf(0)
    private lateinit var prefs: Prefs

    private val askNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { refresh.intValue++ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        prefs.lastReportAt // 첫 실행 시각을 주간 리포트 기준점으로 저장

        val actions = ScreenActions(
            setTracking = { on ->
                prefs.trackingEnabled = on
                if (on) TrackingService.start(this) else TrackingService.stop(this)
                refresh.intValue++
            },
            requestNotifications = {
                if (Build.VERSION.SDK_INT >= 33) askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            },
            openNotificationAccess = { open(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) },
            openUsageAccess = { open(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)) },
            openBatterySettings = {
                open(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
            },
            setGoalMinutes = { prefs.goalMinutes = it; refresh.intValue++ },
            setNightOnly = { prefs.nightOnly = it; refresh.intValue++ },
            setDemo = { prefs.demo = it; refresh.intValue++ },
            clearData = {
                Thread {
                    EventStore.get(this).clear()
                    runOnUiThread { refresh.intValue++ }
                }.start()
            },
        )
        setContent { SleepScreen(refresh.intValue, actions) }

        if (!prefs.askedNotificationPermission && !Permissions.canPostNotifications(this)) {
            prefs.askedNotificationPermission = true
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    override fun onResume() {
        super.onResume()
        if (prefs.trackingEnabled) TrackingService.start(this)
        refresh.intValue++
    }

    private fun open(intent: Intent) {
        try {
            startActivity(intent)
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_SETTINGS))
        }
    }
}
