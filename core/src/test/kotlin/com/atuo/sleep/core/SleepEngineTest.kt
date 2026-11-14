package com.atuo.sleep.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId

class SleepEngineTest {
    private val zone = ZoneId.of("Asia/Seoul")
    private val cfg = SleepConfig(zone = zone, hasNotificationSignal = true)
    private val noSignalCfg = SleepConfig(zone = zone, hasNotificationSignal = false)

    /** 2026-11-[day] [h]:[m] (h 가 24 이상이면 다음 날) */
    private fun t(day: Int, h: Int, m: Int = 0, s: Int = 0): Long =
        LocalDateTime.of(2026, 11, day, 0, 0).plusHours(h.toLong()).plusMinutes(m.toLong()).plusSeconds(s.toLong())
            .atZone(zone).toInstant().toEpochMilli()

    private class Script {
        val events = ArrayList<PhoneEvent>()
        fun add(time: Long, type: EventType) = apply { events += PhoneEvent(time, type) }

        /** 사용자가 잠금을 풀고 [from]~[to] 동안 사용 */
        fun use(from: Long, to: Long) = add(from, EventType.SCREEN_ON).add(from + 2000, EventType.USER_UNLOCK).add(to, EventType.SCREEN_OFF)

        /** 알림이 와서 화면이 8초 켜짐 (잠금 해제 없음) */
        fun notificationWake(at: Long) = add(at - 500, EventType.NOTIFICATION).add(at, EventType.SCREEN_ON).add(at + 8000, EventType.SCREEN_OFF)

        /** 잠금 해제 없이 화면만 5초 켜짐 (알림 없음) */
        fun bareWake(at: Long) = add(at, EventType.SCREEN_ON).add(at + 5000, EventType.SCREEN_OFF)
    }

    private fun detect(s: Script, c: SleepConfig = cfg, now: Long = t(20, 0)) = SleepEngine.detect(s.events, c, now)

    @Test
    fun `screen off to first unlock is one night`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 7), t(14, 7, 20))
        val nights = detect(s)
        assertEquals(1, nights.size)
        val n = nights[0]
        assertEquals(t(13, 23, 30), n.start)
        assertEquals(t(14, 7), n.end)
        assertEquals(LocalDate.of(2026, 11, 14), n.date)
        assertEquals(7 * 60 + 30L, n.asleepMs / 60000)
        assertTrue(n.awakenings.isEmpty())
        assertFalse(n.lowConfidence)
    }

    @Test
    fun `notification screen wakes do not interrupt sleep`() {
        val s = Script().use(t(13, 22), t(13, 23, 30))
            .notificationWake(t(14, 1)).notificationWake(t(14, 3, 15)).notificationWake(t(14, 5, 40))
            .use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertEquals(t(13, 23, 30), n.start)
        assertEquals(t(14, 7), n.end)
        assertEquals(3, n.ignoredWakeCount)
        assertTrue(n.awakenings.isEmpty())
    }

    @Test
    fun `without notification access a wake without unlock is still ignored`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).bareWake(t(14, 2)).bareWake(t(14, 4)).use(t(14, 7), t(14, 7, 20))
        val n = detect(s, noSignalCfg).single()
        assertEquals(7 * 60 + 30L, n.asleepMs / 60000)
        assertEquals(2, n.ignoredWakeCount)
        assertTrue(n.awakenings.isEmpty())
    }

    @Test
    fun `with notification access an unexplained wake counts as a glance`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).notificationWake(t(14, 1)).bareWake(t(14, 4)).use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertEquals(1, n.ignoredWakeCount)
        assertEquals(1, n.awakenings.size)
        assertEquals(AwakeningType.GLANCE, n.awakenings[0].type)
        assertEquals("glance does not shorten sleep", 7 * 60 + 30L, n.asleepMs / 60000)
    }

    @Test
    fun `user unlocking at night is an awakening and is subtracted`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 3), t(14, 3, 10)).use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertEquals(t(13, 23, 30), n.start)
        assertEquals(t(14, 7), n.end)
        assertEquals(1, n.awakenings.size)
        assertEquals(AwakeningType.PHONE_USE, n.awakenings[0].type)
        assertEquals(7 * 60 + 20L, n.asleepMs / 60000)
    }

    @Test
    fun `long awake period mid-night stays one night`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 3), t(14, 3, 40)).use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertEquals(6 * 60 + 50L, n.asleepMs / 60000)
    }

    @Test
    fun `brief check right after putting the phone down moves sleep onset`() {
        // 23:30 에 끄고 23:50 에 다시 5분 사용 → 실제 잠든 건 23:55
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(13, 23, 50), t(13, 23, 55)).use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertEquals(t(13, 23, 55), n.start)
        assertTrue(n.awakenings.isEmpty())
    }

    @Test
    fun `phone-free morning after waking is not counted as sleep`() {
        // 07:00 기상 후 10분 사용, 이후 50분간 폰을 안 봄(씻고 출근) → 기상은 07:00
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 7), t(14, 7, 10)).use(t(14, 8), t(14, 8, 30))
        val n = detect(s).single()
        assertEquals(t(14, 7), n.end)
    }

    @Test
    fun `brief time check in the morning then back to sleep extends the night`() {
        // 06:30 에 2분 확인 후 08:30 까지 다시 잠
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 6, 30), t(14, 6, 32)).use(t(14, 8, 30), t(14, 9))
        val n = detect(s).single()
        assertEquals(t(14, 8, 30), n.end)
        assertEquals(1, n.awakenings.size)
    }

    @Test
    fun `phone left untouched for a whole day is not sleep`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 23), t(14, 23, 30))
        assertTrue(detect(s).isEmpty())
    }

    @Test
    fun `phone-free evening before an hour of phone in bed is not sleep`() {
        // 18:30~22:30 폰 안 봄 → 22:30~23:30 사용 → 취침
        val s = Script().use(t(13, 18), t(13, 18, 30)).use(t(13, 22, 30), t(13, 23, 30)).use(t(14, 7), t(14, 7, 30))
        val n = detect(s).single()
        assertEquals(t(13, 23, 30), n.start)
        assertTrue(n.awakenings.isEmpty())
    }

    @Test
    fun `long phone-free evening does not replace or extend the real night`() {
        // 19:10~01:10 폰 안 봄(6시간) → 01:10~02:20 침대에서 폰 → 02:20~08:00 수면(5시간 40분)
        val s = Script().use(t(13, 18, 20), t(13, 19, 10)).use(t(13, 25, 10), t(13, 26, 20)).use(t(14, 8), t(14, 8, 30))
        val n = detect(s).single()
        assertEquals(t(13, 26, 20), n.start)
        assertEquals(t(14, 8), n.end)
        assertTrue(n.awakenings.isEmpty())
    }

    @Test
    fun `short phone use before dawn after five hours of sleep does not end the night`() {
        // 23:20 취침, 04:30 에 6분 사용, 07:15 기상
        val s = Script().use(t(13, 22), t(13, 23, 20)).use(t(14, 4, 30), t(14, 4, 36)).use(t(14, 7, 15), t(14, 7, 40))
        val n = detect(s).single()
        assertEquals(t(14, 7, 15), n.end)
        assertEquals(1, n.awakenings.size)
    }

    @Test
    fun `daytime phone-free stretch is not sleep`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).use(t(14, 7), t(14, 7, 20))
            .use(t(14, 12), t(14, 13)).use(t(14, 17, 30), t(14, 18))
        val nights = detect(s)
        assertEquals(1, nights.size)
        assertEquals(t(14, 7), nights[0].end)
    }

    @Test
    fun `night shift mode accepts daytime sleep`() {
        val s = Script().use(t(14, 10), t(14, 11)).use(t(14, 18), t(14, 18, 30))
        assertTrue(detect(s).isEmpty())
        assertEquals(1, detect(s, cfg.copy(nightOnly = false)).size)
    }

    @Test
    fun `all-nighter yields no sleep`() {
        val s = Script()
        var time = t(13, 22)
        while (time < t(14, 8)) {
            s.use(time, time + 20 * 60000)
            time += 40 * 60000
        }
        assertTrue(detect(s).isEmpty())
    }

    @Test
    fun `tracking gap marks the night low confidence`() {
        val s = Script().use(t(13, 22), t(13, 23, 30))
            .add(t(14, 6, 50), EventType.TRACK_START)
            .use(t(14, 7), t(14, 7, 20))
        val n = detect(s).single()
        assertTrue(n.lowConfidence)
    }

    @Test
    fun `reboot is not a tracking gap`() {
        val s = Script().use(t(13, 22), t(13, 23, 30))
            .add(t(13, 23, 31), EventType.SHUTDOWN)
            .add(t(14, 7), EventType.BOOT)
            .use(t(14, 7, 1), t(14, 7, 20))
        val n = detect(s).single()
        assertFalse(n.lowConfidence)
        assertEquals(t(13, 23, 30), n.start)
    }

    @Test
    fun `device without lock screen - notification wake ignored, real use counted`() {
        val s = Script()
            .add(t(13, 22), EventType.SCREEN_ON_UNSECURED).add(t(13, 23, 30), EventType.SCREEN_OFF)
            // 알림으로 켜짐 (15초)
            .add(t(14, 2), EventType.NOTIFICATION).add(t(14, 2, 0, 1), EventType.SCREEN_ON_UNSECURED).add(t(14, 2, 0, 16), EventType.SCREEN_OFF)
            // 사용자가 켬
            .add(t(14, 7), EventType.SCREEN_ON_UNSECURED).add(t(14, 7, 20), EventType.SCREEN_OFF)
            .add(t(14, 9), EventType.SCREEN_ON_UNSECURED).add(t(14, 9, 20), EventType.SCREEN_OFF)
        val n = detect(s).single()
        assertEquals(t(13, 23, 30), n.start)
        assertEquals(t(14, 7), n.end)
        assertEquals(1, n.ignoredWakeCount)
    }

    @Test
    fun `screen still on now does not crash and ongoing gap is not reported`() {
        val s = Script().use(t(13, 22), t(13, 23, 30)).add(t(14, 7), EventType.SCREEN_ON).add(t(14, 7, 0, 2), EventType.USER_UNLOCK)
        val n = SleepEngine.detect(s.events, cfg, t(14, 7, 5)).single()
        assertEquals(t(14, 7), n.end)
        // 아직 자는 중(이후 사용 없음)이면 보고하지 않는다
        val sleeping = Script().use(t(13, 22), t(13, 23, 30))
        assertTrue(SleepEngine.detect(sleeping.events, cfg, t(14, 5)).isEmpty())
    }

    @Test
    fun `weekly report averages across midnight and flags irregularity`() {
        val s = Script()
        // 6일간: 취침 23:30 / 00:30 번갈아, 기상 07:00
        for (i in 0 until 6) {
            val day = 13 + i
            val bed = if (i % 2 == 0) t(day, 23, 30) else t(day, 24, 30)
            s.use(bed - 60 * 60000, bed).use(t(day + 1, 7), t(day + 1, 7, 30))
            s.use(t(day + 1, 12), t(day + 1, 12, 30)).use(t(day + 1, 18), t(day + 1, 18, 30))
        }
        val nights = SleepEngine.detect(s.events, cfg, t(20, 0))
        assertEquals(6, nights.size)
        val report = WeeklyAnalyzer.analyze(nights, LocalDate.of(2026, 11, 19), zone, 8 * SleepConfig.HOUR)
        assertNotNull(report)
        report!!
        assertEquals(0, report.avgBedMinute) // 00:00
        assertEquals(7 * 60, report.avgWakeMinute)
        assertEquals(30, report.bedSdMin)
        assertEquals(0, report.wakeSdMin)
        assertEquals(7 * 60L, report.avgAsleepMs / 60000)
        assertEquals(6 * 60L, report.sleepDebtMs / 60000)
        assertTrue(report.score in 1..100)
        assertTrue(report.insights.isNotEmpty())
    }

    @Test
    fun `circular helpers`() {
        assertEquals(0, WeeklyAnalyzer.circularMean(listOf(1410, 30)))
        assertEquals(60, WeeklyAnalyzer.wrapDiff(30, 1410))
        assertEquals(-60, WeeklyAnalyzer.wrapDiff(1410, 30))
        assertEquals("7시간 30분", Fmt.duration(450 * 60000L))
    }
}
