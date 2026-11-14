package com.atuo.sleep.track

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.atuo.sleep.MainActivity
import com.atuo.sleep.R
import com.atuo.sleep.core.Fmt
import com.atuo.sleep.data.Permissions
import com.atuo.sleep.data.Prefs
import com.atuo.sleep.data.SleepRepository
import java.time.LocalTime

/** 7일마다 한 번, 아침에 폰을 처음 열 때 주간 수면 리포트를 알림으로 보낸다. */
object WeeklyNotifier {
    private const val CHANNEL = "weekly"
    private const val WEEK_MS = 7L * 24 * 60 * 60 * 1000

    fun maybeNotify(context: Context) {
        val prefs = Prefs(context)
        val now = System.currentTimeMillis()
        if (now - prefs.lastReportAt < WEEK_MS) return
        if (LocalTime.now().hour < 6) return // 새벽에 깬 사람을 알림으로 방해하지 않는다
        if (prefs.demo || !Permissions.canPostNotifications(context)) return
        val report = SleepRepository.load(context, now).report ?: return

        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "주간 수면 리포트", NotificationManager.IMPORTANCE_DEFAULT)
        )
        val summary = "평균 ${Fmt.duration(report.avgAsleepMs)} · 취침 ${Fmt.minuteOfDay(report.avgBedMinute)} · 기상 ${Fmt.minuteOfDay(report.avgWakeMinute)}"
        val open = PendingIntent.getActivity(
            context, 1, Intent(context, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_moon)
            .setContentTitle("이번 주 수면 리포트 · ${report.score}점")
            .setContentText(summary)
            .setStyle(NotificationCompat.BigTextStyle().bigText((listOf(summary) + report.insights).joinToString("\n")))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        nm.notify(2, notification)
        prefs.lastReportAt = now
    }
}
