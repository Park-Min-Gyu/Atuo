package com.atuo.sleep.data

import com.atuo.sleep.core.AwakeningType
import com.atuo.sleep.core.SleepConfig
import com.atuo.sleep.core.SleepEngine
import com.atuo.sleep.core.WeeklyAnalyzer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class DemoDataTest {
    @Test
    fun `demo week produces seven nights with awakenings and ignored notification wakes`() {
        val zone = ZoneId.of("Asia/Seoul")
        val today = LocalDate.of(2026, 11, 14)
        val now = today.atTime(21, 0).atZone(zone).toInstant().toEpochMilli()
        val cfg = SleepConfig(zone = zone, hasNotificationSignal = true)
        val nights = SleepEngine.detect(DemoData.events(now, zone), cfg, now)
        assertEquals(7, nights.size)
        assertTrue(nights.all { it.ignoredWakeCount >= 1 })
        assertEquals(2, nights.count { n -> n.awakenings.any { it.type == AwakeningType.PHONE_USE } })
        assertEquals(1, nights.count { n -> n.awakenings.any { it.type == AwakeningType.GLANCE } })
        val report = WeeklyAnalyzer.analyze(nights, today, zone, 450 * SleepConfig.MINUTE)
        assertNotNull(report)
        assertTrue(report!!.score in 1..100)
        assertTrue(report.insights.isNotEmpty())
    }
}
