package com.atuo.sleep.data

import android.content.Context
import com.atuo.sleep.core.SleepConfig
import com.atuo.sleep.core.SleepEngine
import com.atuo.sleep.core.SleepNight
import com.atuo.sleep.core.WeeklyAnalyzer
import com.atuo.sleep.core.WeeklyReport
import java.time.LocalDate
import java.time.ZoneId

data class Analysis(
    val today: LocalDate,
    val zone: ZoneId,
    val nights: List<SleepNight>,
    val report: WeeklyReport?,
    val demo: Boolean,
)

object SleepRepository {
    private const val DAY = 24 * SleepConfig.HOUR

    /** DB 를 읽고 분석한다. 메인 스레드 밖에서 호출할 것. */
    fun load(context: Context, now: Long = System.currentTimeMillis()): Analysis {
        val prefs = Prefs(context)
        val zone = ZoneId.systemDefault()
        val demo = prefs.demo
        val events = if (demo) DemoData.events(now, zone) else EventStore.get(context).since(now - 16 * DAY)
        val cfg = SleepConfig(
            zone = zone,
            hasNotificationSignal = demo || Permissions.notificationAccess(context),
            nightOnly = prefs.nightOnly,
        )
        val nights = SleepEngine.detect(events, cfg, now)
        val today = LocalDate.now(zone)
        val report = WeeklyAnalyzer.analyze(nights, today, zone, prefs.goalMinutes * SleepConfig.MINUTE)
        return Analysis(today, zone, nights, report, demo)
    }
}
