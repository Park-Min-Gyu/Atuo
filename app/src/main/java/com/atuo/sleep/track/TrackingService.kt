package com.atuo.sleep.track

import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.atuo.sleep.MainActivity
import com.atuo.sleep.R
import com.atuo.sleep.core.EventType
import com.atuo.sleep.data.EventStore
import com.atuo.sleep.data.Prefs
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * 화면 켜짐/꺼짐과 잠금 해제를 기록하는 상시 서비스.
 *
 * - ACTION_SCREEN_ON  : 화면이 켜짐. 알림·알람·사용자 모두 원인이 될 수 있어 이것만으로는 "깼다"고 보지 않는다.
 * - ACTION_USER_PRESENT: 사용자가 잠금을 직접 풀었을 때만 온다 → 깨어 있다는 확실한 증거.
 * - ACTION_SCREEN_OFF : 화면이 꺼짐 → 잠든 시각 후보.
 */
class TrackingService : Service() {
    private lateinit var io: ExecutorService
    private lateinit var store: EventStore

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val now = System.currentTimeMillis()
            when (intent.action) {
                Intent.ACTION_SCREEN_ON -> {
                    ScreenState.lastScreenOnAt = now
                    val locked = (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
                    record(if (locked) EventType.SCREEN_ON else EventType.SCREEN_ON_UNSECURED, now)
                }
                Intent.ACTION_SCREEN_OFF -> record(EventType.SCREEN_OFF, now)
                Intent.ACTION_USER_PRESENT -> {
                    record(EventType.USER_UNLOCK, now)
                    io.execute { WeeklyNotifier.maybeNotify(applicationContext) }
                }
                Intent.ACTION_SHUTDOWN -> record(EventType.SHUTDOWN, now)
                Intent.ACTION_POWER_CONNECTED, Intent.ACTION_POWER_DISCONNECTED -> record(EventType.POWER, now)
            }
        }
    }

    private fun record(type: EventType, time: Long) {
        io.execute { store.add(type, time) }
    }

    override fun onCreate() {
        super.onCreate()
        io = Executors.newSingleThreadExecutor()
        store = EventStore.get(this)
        startInForeground()

        io.execute { resume() } // 리시버보다 먼저 큐에 넣어 이벤트 순서를 보장
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_USER_PRESENT)
            addAction(Intent.ACTION_SHUTDOWN)
            addAction(Intent.ACTION_POWER_CONNECTED)
            addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        ContextCompat.registerReceiver(this, receiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
    }

    /** 서비스가 (재)시작될 때: 꺼져 있던 구간을 메우거나 공백으로 표시하고, 현재 화면 상태를 기록한다. */
    private fun resume() {
        val now = System.currentTimeMillis()
        val last = store.lastTime()
        if (last == null) {
            store.add(EventType.TRACK_START, now)
        } else {
            val rebooted = now - last > SystemClock.elapsedRealtime()
            val backfilled = UsageBackfill.fill(this, last + 1, now - 1)
            when {
                rebooted -> store.add(EventType.BOOT, now)
                !backfilled -> store.add(EventType.TRACK_START, now)
            }
        }
        val interactive = (getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive
        val locked = (getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager).isKeyguardLocked
        when {
            !interactive -> store.add(EventType.SCREEN_OFF, now + 1)
            locked -> store.add(EventType.SCREEN_ON, now + 1)
            else -> {
                // 화면이 켜져 있고 잠금이 풀려 있음 = 지금 사용자가 쓰는 중
                store.add(EventType.SCREEN_ON, now + 1)
                store.add(EventType.USER_UNLOCK, now + 2)
            }
        }
        store.prune(now - KEEP_MS)
    }

    private fun startInForeground() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "수면 추적", NotificationManager.IMPORTANCE_MIN).apply {
                description = "화면 켜짐/꺼짐을 기록하는 동안 표시됩니다"
                setShowBadge(false)
            }
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_moon)
            .setContentTitle("수면 패턴 기록 중")
            .setContentText("폰을 끄고 켜는 시각으로 수면을 추정해요")
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // startForegroundService() 로 호출될 때마다 startForeground() 를 다시 불러야 시스템이 앱을 종료시키지 않는다.
        startInForeground()
        return START_STICKY
    }

    override fun onDestroy() {
        unregisterReceiver(receiver)
        io.shutdown()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL = "tracking"
        private const val NOTIFICATION_ID = 1
        private const val KEEP_MS = 60L * 24 * 60 * 60 * 1000

        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, TrackingService::class.java))
            } catch (_: Exception) {
                // 백그라운드에서 시작이 거부된 경우: 다음에 앱을 열 때 다시 시작된다.
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, TrackingService::class.java))
        }
    }
}
