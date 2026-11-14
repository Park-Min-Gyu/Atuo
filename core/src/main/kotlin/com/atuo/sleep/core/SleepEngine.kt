package com.atuo.sleep.core

import java.time.Instant
import java.time.LocalDate
import java.time.ZonedDateTime

/**
 * 화면 켜짐/꺼짐·잠금 해제 이벤트만으로 수면 구간을 추정한다.
 *
 * 1) 이벤트 → 화면 세션(켜짐~꺼짐)으로 묶고 원인을 판정 (사용자 / 알림 / 잠깐 확인 / 불명)
 * 2) 사용자가 실제로 쓴 세션 사이의 빈 구간 = 수면 후보 조각
 * 3) 짧게 깼다 다시 잔 조각들을 한 밤으로 연결
 * 4) 날짜별로 가장 긴 수면 하나를 그날 밤의 수면으로 선택
 */
object SleepEngine {

    data class Timeline(val sessions: List<ScreenSession>, val untracked: List<Interval>)

    private class Open(val start: Long, val unsecured: Boolean, var unlocked: Boolean)

    fun buildTimeline(events: List<PhoneEvent>, cfg: SleepConfig, now: Long): Timeline {
        val sorted = events.filter { it.time <= now }.sortedBy { it.time }
        if (sorted.isEmpty()) return Timeline(emptyList(), emptyList())

        val signals = sorted
            .filter { it.type == EventType.NOTIFICATION || it.type == EventType.POWER }
            .map { it.time }
            .toLongArray()

        val sessions = ArrayList<ScreenSession>()
        val untracked = ArrayList<Interval>()
        var open: Open? = null
        var last = sorted.first().time

        fun close(end: Long) {
            val o = open ?: return
            sessions += ScreenSession(o.start, maxOf(end, o.start), classify(o, maxOf(end, o.start), signals, cfg))
            open = null
        }

        sorted.forEachIndexed { i, e ->
            when (e.type) {
                EventType.SCREEN_ON ->
                    if (open == null) open = Open(e.time, unsecured = false, unlocked = false)
                EventType.SCREEN_ON_UNSECURED ->
                    if (open == null) open = Open(e.time, unsecured = true, unlocked = false)
                EventType.USER_UNLOCK -> {
                    val o = open
                    if (o == null) open = Open(e.time, unsecured = false, unlocked = true) else o.unlocked = true
                }
                EventType.SCREEN_OFF, EventType.SHUTDOWN -> close(e.time)
                EventType.TRACK_START -> {
                    // 서비스가 죽어 있던 구간: 열려 있던 세션은 마지막으로 확인된 시각에 닫는다.
                    close(last)
                    if (i > 0 && e.time > last) untracked += Interval(last, e.time)
                }
                EventType.BOOT -> close(last)
                EventType.NOTIFICATION, EventType.POWER -> Unit
            }
            last = e.time
        }
        close(now) // 지금도 화면이 켜져 있는 경우
        return Timeline(sessions, untracked)
    }

    private fun classify(o: Open, end: Long, signals: LongArray, cfg: SleepConfig): WakeCause {
        if (o.unlocked) return WakeCause.USER
        val duration = end - o.start
        val correlated = hasSignalBetween(signals, o.start - cfg.correlationBeforeMs, o.start + cfg.correlationAfterMs)
        if (o.unsecured) {
            // 잠금화면이 없으면 "잠금 해제" 신호가 없으므로 알림 상관관계와 켜져 있던 시간으로 판정한다.
            return when {
                correlated && duration < cfg.unsecuredPassiveMaxMs -> WakeCause.NOTIFICATION
                cfg.hasNotificationSignal || duration >= cfg.unsecuredActiveMinMs -> WakeCause.USER
                else -> WakeCause.UNKNOWN
            }
        }
        return when {
            correlated -> WakeCause.NOTIFICATION
            cfg.hasNotificationSignal -> WakeCause.GLANCE
            else -> WakeCause.UNKNOWN
        }
    }

    private fun hasSignalBetween(sortedTimes: LongArray, from: Long, to: Long): Boolean {
        var lo = 0
        var hi = sortedTimes.size
        while (lo < hi) {
            val mid = (lo + hi) ushr 1
            if (sortedTimes[mid] < from) lo = mid + 1 else hi = mid
        }
        return lo < sortedTimes.size && sortedTimes[lo] <= to
    }

    fun detect(events: List<PhoneEvent>, cfg: SleepConfig, now: Long): List<SleepNight> {
        val timeline = buildTimeline(events, cfg, now)
        val active = timeline.sessions.filter { it.cause == WakeCause.USER }.sortedBy { it.start }
        if (active.size < 2) return emptyList()

        // 사용 세션 사이의 무사용 구간 중 충분히 긴 것 = 수면 조각
        val fragments = ArrayList<Interval>()
        for (i in 0 until active.size - 1) {
            val gap = Interval(active[i].end, active[i + 1].start)
            if (gap.durationMs < cfg.minFragmentMs) continue
            // 저녁/낮에 폰을 안 본 시간이 밤잠 앞뒤에 붙지 않도록 조각 단위로도 밤 시간대를 확인한다.
            if (cfg.nightOnly && !mostlyAtNight(gap.start, gap.end, cfg)) continue
            fragments += gap
        }

        // 조각 사이 깨어 있던 시간이 짧으면 한 밤으로 연결
        val chains = ArrayList<MutableList<Interval>>()
        var asleepSoFar = 0L
        for (f in fragments) {
            val current = chains.lastOrNull()
            if (current != null && shouldLink(current, f, asleepSoFar, cfg)) {
                current += f
                asleepSoFar += f.durationMs
            } else {
                chains += mutableListOf(f)
                asleepSoFar = f.durationMs
            }
        }

        val candidates = chains.mapNotNull { chain ->
            trimEdges(chain, cfg)
            toNight(chain, timeline, cfg)
        }

        // 같은 날 후보가 여럿이면(예: 저녁 내내 폰을 안 봄 + 실제 밤잠) 한밤중과 더 많이 겹치는 쪽이 밤잠이다.
        return candidates
            .groupBy { it.date }
            .map { (_, sameDay) ->
                if (cfg.nightOnly) {
                    sameDay.maxWithOrNull(
                        compareBy<SleepNight> {
                            windowOverlapMs(it.start, it.end, cfg.coreNightStartHour, cfg.coreNightEndHour, cfg)
                        }.thenBy { it.asleepMs }
                    )!!
                } else {
                    sameDay.maxByOrNull { it.asleepMs }!!
                }
            }
            .sortedBy { it.start }
    }

    private fun shouldLink(chain: List<Interval>, next: Interval, asleepSoFar: Long, cfg: SleepConfig): Boolean {
        val awake = next.start - chain.last().end
        // 이미 충분히 잔 뒤 아침에 폰을 켰다면 기상이다. 아주 잠깐 확인한 경우만 "다시 잠듦".
        // (새벽 4시에 잠깐 쓴 것은 아침이 아니므로 아래의 일반 규칙을 따른다)
        if (asleepSoFar >= cfg.mainSleepMs) {
            val wakeHour = Instant.ofEpochMilli(chain.last().end).atZone(cfg.zone).hour
            if (!cfg.nightOnly || wakeHour >= cfg.morningStartHour) return awake <= cfg.lateInterruptionMs
        }
        if (awake <= cfg.briefInterruptionMs) return true
        // 한밤중에 오래 깨어 있던 경우. 저녁에 시작한 조각은 "폰 안 본 저녁 → 침대에서 폰 → 취침"일 수 있어 제외.
        if (!cfg.nightOnly || awake > cfg.maxInterruptionMs) return false
        val startHour = Instant.ofEpochMilli(chain.first().start).atZone(cfg.zone).hour
        return startHour !in 12 until cfg.eveningEndHour
    }

    /** 맨 앞/뒤의 짧은 조각(90분 미만, 또는 옆의 깨어 있던 시간의 3배 미만)은 "자기 전/일어난 뒤 잠깐 폰을 안 본 시간"일 가능성이 높아 제외한다. */
    private fun trimEdges(chain: MutableList<Interval>, cfg: SleepConfig) {
        while (chain.size > 1) {
            val first = chain[0]
            val awakeAfter = chain[1].start - first.end
            if (first.durationMs < maxOf(cfg.minEdgeFragmentMs, 3 * awakeAfter)) chain.removeAt(0) else break
        }
        while (chain.size > 1) {
            val lastFrag = chain[chain.size - 1]
            val awakeBefore = lastFrag.start - chain[chain.size - 2].end
            if (lastFrag.durationMs < maxOf(cfg.minEdgeFragmentMs, 3 * awakeBefore)) {
                chain.removeAt(chain.size - 1)
            } else break
        }
    }

    private fun toNight(chain: List<Interval>, timeline: Timeline, cfg: SleepConfig): SleepNight? {
        val start = chain.first().start
        val end = chain.last().end
        val asleep = chain.sumOf { it.durationMs }
        if (asleep < cfg.minSleepMs || asleep > cfg.maxSleepMs) return null
        if (cfg.nightOnly && !mostlyAtNight(start, end, cfg)) return null

        val awakenings = ArrayList<Awakening>()
        for (i in 0 until chain.size - 1) {
            awakenings += Awakening(chain[i].end, chain[i + 1].start, AwakeningType.PHONE_USE)
        }
        var ignored = 0
        for (s in timeline.sessions) {
            if (s.cause == WakeCause.USER) continue
            if (chain.none { s.start >= it.start && s.start < it.end }) continue
            if (s.cause == WakeCause.GLANCE) awakenings += Awakening(s.start, s.end, AwakeningType.GLANCE)
            else ignored++
        }
        awakenings.sortBy { it.start }

        val whole = Interval(start, end)
        return SleepNight(
            date = sleepDate(start, end, cfg),
            start = start,
            end = end,
            asleepMs = asleep,
            awakenings = awakenings,
            ignoredWakeCount = ignored,
            lowConfidence = timeline.untracked.any { it.overlaps(whole) },
        )
    }

    private fun sleepDate(start: Long, end: Long, cfg: SleepConfig): LocalDate {
        val mid = Instant.ofEpochMilli(start + (end - start) / 2).atZone(cfg.zone)
        return if (mid.hour >= cfg.dayBoundaryHour) mid.toLocalDate().plusDays(1) else mid.toLocalDate()
    }

    private fun mostlyAtNight(start: Long, end: Long, cfg: SleepConfig): Boolean =
        windowOverlapMs(start, end, cfg.nightStartHour, cfg.nightEndHour, cfg) * 3 >= (end - start) * 2

    /** [start, end] 가 매일 fromHour ~ 다음날 toHour 창과 겹치는 총 시간. */
    private fun windowOverlapMs(start: Long, end: Long, fromHour: Int, toHour: Int, cfg: SleepConfig): Long {
        val first = Instant.ofEpochMilli(start).atZone(cfg.zone).toLocalDate().minusDays(1)
        val lastDay = Instant.ofEpochMilli(end).atZone(cfg.zone).toLocalDate()
        var total = 0L
        var day = first
        while (!day.isAfter(lastDay)) {
            val nightStart = ZonedDateTime.of(day.atTime(fromHour, 0), cfg.zone).toInstant().toEpochMilli()
            val nightEnd = ZonedDateTime.of(day.plusDays(1).atTime(toHour, 0), cfg.zone).toInstant().toEpochMilli()
            total += maxOf(0L, minOf(end, nightEnd) - maxOf(start, nightStart))
            day = day.plusDays(1)
        }
        return total
    }
}
