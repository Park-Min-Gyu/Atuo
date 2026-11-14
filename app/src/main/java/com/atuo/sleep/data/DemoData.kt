package com.atuo.sleep.data

import com.atuo.sleep.core.EventType
import com.atuo.sleep.core.PhoneEvent
import java.time.Instant
import java.time.ZoneId

/** 설치 직후 화면이 어떻게 보이는지 확인하기 위한 예시 일주일. */
object DemoData {
    private const val MIN = 60_000L

    // 23:00 기준 취침(분), 기상 시각(분), 새벽에 폰을 쓴 시간(분, 0 이면 없음)
    private val bedAfter23 = intArrayOf(40, 95, 20, 150, 65, 200, 170)
    private val wakeMinute = intArrayOf(430, 425, 435, 420, 440, 580, 610)
    private val nightUse = intArrayOf(0, 0, 6, 0, 0, 12, 0)

    fun events(now: Long, zone: ZoneId): List<PhoneEvent> {
        val today = Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
        val out = ArrayList<PhoneEvent>()
        fun use(from: Long, to: Long) {
            out += PhoneEvent(from, EventType.SCREEN_ON)
            out += PhoneEvent(from + 1500, EventType.USER_UNLOCK)
            out += PhoneEvent(to, EventType.SCREEN_OFF)
        }
        fun notification(at: Long) {
            out += PhoneEvent(at - 400, EventType.NOTIFICATION)
            out += PhoneEvent(at, EventType.SCREEN_ON)
            out += PhoneEvent(at + 8000, EventType.SCREEN_OFF)
        }
        for (i in 0 until 7) {
            val wakeDay = today.minusDays((6 - i).toLong())
            val midnight = wakeDay.atStartOfDay(zone).toInstant().toEpochMilli()
            val bed = midnight - 60 * MIN + bedAfter23[i] * MIN
            val wake = midnight + wakeMinute[i] * MIN
            use(bed - 70 * MIN, bed)
            notification(midnight + 150 * MIN + i * 7 * MIN)
            if (i % 2 == 0) notification(midnight + 320 * MIN)
            if (nightUse[i] > 0) use(midnight + 270 * MIN, midnight + (270 + nightUse[i]) * MIN)
            if (i == 3) { // 알림 없이 화면만 잠깐 켬 = 시간 확인
                out += PhoneEvent(midnight + 260 * MIN, EventType.SCREEN_ON)
                out += PhoneEvent(midnight + 260 * MIN + 5000, EventType.SCREEN_OFF)
            }
            use(wake, wake + 25 * MIN)
            use(midnight + 750 * MIN, midnight + 790 * MIN)
            use(midnight + 1200 * MIN, midnight + 1240 * MIN)
        }
        return out
    }
}
