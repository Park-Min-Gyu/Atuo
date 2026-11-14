package com.atuo.sleep.core

import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.roundToLong
import kotlin.math.sin
import kotlin.math.sqrt

data class WeeklyReport(
    val from: LocalDate,
    val to: LocalDate,
    /** 기간 내 기록된 밤 (날짜순). */
    val nights: List<SleepNight>,
    val avgAsleepMs: Long,
    /** 평균 취침/기상 시각 (하루 중 분, 0~1439). 자정을 넘나드는 값도 올바르게 평균낸다. */
    val avgBedMinute: Int,
    val avgWakeMinute: Int,
    /** 취침/기상 시각의 표준편차(분). 기록이 2일 미만이면 null. */
    val bedSdMin: Int?,
    val wakeSdMin: Int?,
    val weekdayAvgMs: Long?,
    val weekendAvgMs: Long?,
    /** 주말과 평일의 수면 중간 시각 차이(분). 양수면 주말에 더 늦다. */
    val socialJetlagMin: Int?,
    /** 목표 대비 부족분의 합. */
    val sleepDebtMs: Long,
    val shortest: SleepNight,
    val longest: SleepNight,
    val totalAwakenings: Int,
    val ignoredWakes: Int,
    /** 0~100. 수면 시간 60% + 규칙성 40%. */
    val score: Int,
    val insights: List<String>,
)

object WeeklyAnalyzer {

    /** [endDate] 를 마지막 날로 하는 7일간의 리포트. 기록이 하나도 없으면 null. */
    fun analyze(allNights: List<SleepNight>, endDate: LocalDate, zone: ZoneId, goalMs: Long): WeeklyReport? {
        val from = endDate.minusDays(6)
        val nights = allNights.filter { !it.date.isBefore(from) && !it.date.isAfter(endDate) }.sortedBy { it.date }
        if (nights.isEmpty()) return null

        val bedMinutes = nights.map { minuteOfDay(it.start, zone) }
        val wakeMinutes = nights.map { minuteOfDay(it.end, zone) }
        val avgBed = circularMean(bedMinutes)
        val avgWake = circularMean(wakeMinutes)
        val bedSd = if (nights.size >= 2) circularSd(bedMinutes, avgBed) else null
        val wakeSd = if (nights.size >= 2) circularSd(wakeMinutes, avgWake) else null
        val avgAsleep = nights.map { it.asleepMs }.average().roundToLong()

        val weekend = nights.filter { it.date.dayOfWeek == DayOfWeek.SATURDAY || it.date.dayOfWeek == DayOfWeek.SUNDAY }
        val weekday = nights - weekend.toSet()
        val jetlag = if (weekend.isNotEmpty() && weekday.isNotEmpty()) {
            val free = circularMean(weekend.map { minuteOfDay(it.start + it.inBedMs / 2, zone) })
            val work = circularMean(weekday.map { minuteOfDay(it.start + it.inBedMs / 2, zone) })
            wrapDiff(free, work)
        } else null

        val debt = nights.sumOf { maxOf(0L, goalMs - it.asleepMs) }
        val durationScore = (avgAsleep.toDouble() / goalMs).coerceAtMost(1.0) * 100
        val score = if (bedSd != null && wakeSd != null) {
            val sd = (bedSd + wakeSd) / 2.0
            val regularity = ((120 - sd) / 100.0).coerceIn(0.0, 1.0) * 100 // 20분 이하 만점, 120분 이상 0점
            (durationScore * 0.6 + regularity * 0.4).roundToInt()
        } else durationScore.roundToInt()

        val report = WeeklyReport(
            from = from,
            to = endDate,
            nights = nights,
            avgAsleepMs = avgAsleep,
            avgBedMinute = avgBed,
            avgWakeMinute = avgWake,
            bedSdMin = bedSd,
            wakeSdMin = wakeSd,
            weekdayAvgMs = weekday.takeIf { it.isNotEmpty() }?.map { it.asleepMs }?.average()?.roundToLong(),
            weekendAvgMs = weekend.takeIf { it.isNotEmpty() }?.map { it.asleepMs }?.average()?.roundToLong(),
            socialJetlagMin = jetlag,
            sleepDebtMs = debt,
            shortest = nights.minByOrNull { it.asleepMs }!!,
            longest = nights.maxByOrNull { it.asleepMs }!!,
            totalAwakenings = nights.sumOf { it.awakenings.size },
            ignoredWakes = nights.sumOf { it.ignoredWakeCount },
            score = score,
            insights = emptyList(),
        )
        return report.copy(insights = buildInsights(report, goalMs))
    }

    private fun buildInsights(r: WeeklyReport, goalMs: Long): List<String> {
        val out = ArrayList<String>()
        val n = r.nights.size
        if (n < 7) out += "최근 7일 중 ${n}일 밤이 기록됐어요. 기록이 쌓일수록 분석이 정확해져요."

        val diff = goalMs - r.avgAsleepMs
        out += when {
            diff > 15 * SleepConfig.MINUTE ->
                "하루 평균 ${Fmt.duration(r.avgAsleepMs)} 잤어요. 목표(${Fmt.duration(goalMs)})보다 ${Fmt.duration(diff)} 부족해요."
            else -> "하루 평균 ${Fmt.duration(r.avgAsleepMs)} 잤어요. 목표 수면 시간을 채우고 있어요."
        }
        if (r.sleepDebtMs >= 2 * SleepConfig.HOUR) {
            out += "이번 주 누적 수면 부족은 ${Fmt.duration(r.sleepDebtMs)}이에요."
        }

        r.bedSdMin?.let { sd ->
            if (sd >= 60) {
                out += "잠드는 시각이 날마다 ±${Fmt.duration(sd * SleepConfig.MINUTE)}씩 흔들려요. 매일 비슷한 시각에 폰을 내려놓아 보세요."
            } else if (sd <= 30) {
                out += "잠드는 시각이 일정해요(±${sd}분). 좋은 습관이에요."
            }
        }

        if (r.avgBedMinute in 60 until 360) {
            out += "평균적으로 ${Fmt.minuteOfDay(r.avgBedMinute)}에야 폰을 끄고 잠들어요. 30분만 앞당겨도 수면 시간이 늘어나요."
        }

        r.socialJetlagMin?.let { j ->
            if (abs(j) >= 60) {
                val dir = if (j > 0) "늦게" else "일찍"
                out += "주말에는 평일보다 ${Fmt.duration(abs(j) * SleepConfig.MINUTE)} $dir 자고 일어나요. 차이가 1시간을 넘으면 월요일이 힘들어져요."
            }
        }

        if (r.totalAwakenings >= 2 * n) {
            out += "자다가 폰을 확인한 횟수가 하루 평균 ${"%.1f".format(r.totalAwakenings.toDouble() / n)}회예요."
        }
        if (r.ignoredWakes > 0) {
            out += "알림 때문에 화면만 켜진 ${r.ignoredWakes}회는 깬 것으로 치지 않았어요."
        }
        if (n >= 3 && r.longest.asleepMs - r.shortest.asleepMs >= 2 * SleepConfig.HOUR) {
            out += "가장 짧게 잔 날(${Fmt.dayLabel(r.shortest.date)} ${Fmt.duration(r.shortest.asleepMs)})과 " +
                "가장 길게 잔 날(${Fmt.dayLabel(r.longest.date)} ${Fmt.duration(r.longest.asleepMs)})의 차이가 커요."
        }
        return out
    }

    fun minuteOfDay(epochMs: Long, zone: ZoneId): Int {
        val t = Instant.ofEpochMilli(epochMs).atZone(zone)
        return t.hour * 60 + t.minute
    }

    /** 23:30 과 00:30 의 평균이 00:00 이 되도록 원형 평균을 쓴다. */
    fun circularMean(minutes: List<Int>): Int {
        var s = 0.0
        var c = 0.0
        for (m in minutes) {
            val a = m / 1440.0 * 2 * PI
            s += sin(a)
            c += cos(a)
        }
        val mean = (atan2(s, c) / (2 * PI) * 1440).roundToInt()
        return ((mean % 1440) + 1440) % 1440
    }

    /** a - b 를 -720..720 분 범위로 감싼 차이. */
    fun wrapDiff(a: Int, b: Int): Int {
        var d = (a - b) % 1440
        if (d > 720) d -= 1440
        if (d < -720) d += 1440
        return d
    }

    private fun circularSd(minutes: List<Int>, mean: Int): Int =
        sqrt(minutes.map { wrapDiff(it, mean).toDouble().let { d -> d * d } }.average()).roundToInt()
}

object Fmt {
    private val DAYS = arrayOf("월", "화", "수", "목", "금", "토", "일")

    fun duration(ms: Long): String {
        val totalMin = (ms + 30_000) / 60_000
        val h = totalMin / 60
        val m = totalMin % 60
        return when {
            h == 0L -> "${m}분"
            m == 0L -> "${h}시간"
            else -> "${h}시간 ${m}분"
        }
    }

    fun minuteOfDay(minute: Int): String = "%02d:%02d".format(minute / 60, minute % 60)

    fun time(epochMs: Long, zone: ZoneId): String = minuteOfDay(WeeklyAnalyzer.minuteOfDay(epochMs, zone))

    fun dayOfWeek(date: LocalDate): String = DAYS[date.dayOfWeek.value - 1]

    fun dayLabel(date: LocalDate): String = "${date.monthValue}/${date.dayOfMonth}(${dayOfWeek(date)})"
}
