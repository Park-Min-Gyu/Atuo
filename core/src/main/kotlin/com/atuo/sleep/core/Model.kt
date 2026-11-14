package com.atuo.sleep.core

import java.time.LocalDate
import java.time.ZoneId

/** 폰에서 수집하는 원시 이벤트. 시각(epoch ms)과 종류만 저장한다. */
enum class EventType(val code: Int) {
    /** 화면이 켜짐 (잠금 상태). 알림·알람·사용자 모두 원인이 될 수 있다. */
    SCREEN_ON(1),

    /** 화면이 켜졌는데 잠금화면이 없는 상태 (잠금 미설정, 또는 잠금 유예 시간 내). */
    SCREEN_ON_UNSECURED(2),
    SCREEN_OFF(3),

    /** 사용자가 직접 잠금을 해제함. "깨어 있음"의 확실한 증거. */
    USER_UNLOCK(4),

    /** 알림이 게시됨 (알림 접근 권한이 있을 때만 수집). 내용은 저장하지 않는다. */
    NOTIFICATION(5),

    /** 충전기 연결/분리 — 화면을 깨우는 또 다른 비사용자 원인. */
    POWER(6),

    /** 추적 서비스가 (재)시작됨. 직전 이벤트부터 이 시각까지는 기록 공백이다. */
    TRACK_START(7),

    /** 재부팅 후 시작됨. 그 사이 폰은 꺼져 있었으므로 공백으로 보지 않는다. */
    BOOT(8),
    SHUTDOWN(9);

    companion object {
        fun fromCode(code: Int): EventType? = entries.firstOrNull { it.code == code }
    }
}

data class PhoneEvent(val time: Long, val type: EventType)

/** 화면이 켜진 원인 판정 결과. */
enum class WakeCause {
    /** 사용자가 잠금을 풀고 사용함 → 깨어 있음. */
    USER,

    /** 알림/충전 때문에 화면만 켜짐 → 무시. */
    NOTIFICATION,

    /** 잠금은 안 풀었지만 알림도 없었음 → 사용자가 시간 등을 잠깐 확인한 것으로 추정. */
    GLANCE,

    /** 잠금 해제 없음 + 알림 신호를 수집할 수 없어 원인 불명 → 무시. */
    UNKNOWN,
}

data class ScreenSession(val start: Long, val end: Long, val cause: WakeCause) {
    val durationMs: Long get() = end - start
}

data class Interval(val start: Long, val end: Long) {
    val durationMs: Long get() = end - start
    fun overlaps(other: Interval): Boolean = start < other.end && other.start < end
}

enum class AwakeningType { PHONE_USE, GLANCE }

/** 수면 도중 깬 기록. PHONE_USE 는 수면 시간에서 제외되고 GLANCE 는 횟수만 센다. */
data class Awakening(val start: Long, val end: Long, val type: AwakeningType) {
    val durationMs: Long get() = end - start
}

data class SleepNight(
    /** 기상한 쪽 날짜 (예: 금→토 밤이면 토요일). */
    val date: LocalDate,
    /** 잠든 시각 = 마지막으로 폰 화면이 꺼진 시각. */
    val start: Long,
    /** 일어난 시각 = 이후 처음으로 사용자가 폰을 켠 시각. */
    val end: Long,
    /** 중간에 폰을 사용한 시간을 뺀 실제 수면 시간. */
    val asleepMs: Long,
    val awakenings: List<Awakening>,
    /** 수면 중 알림 등으로 화면만 켜졌다가 무시된 횟수. */
    val ignoredWakeCount: Int,
    /** 추적이 끊긴 구간과 겹쳐 신뢰도가 낮은 기록. */
    val lowConfidence: Boolean,
) {
    val inBedMs: Long get() = end - start
}

data class SleepConfig(
    val zone: ZoneId = ZoneId.systemDefault(),
    /** 알림 접근 권한이 있어 NOTIFICATION 이벤트가 수집되는지. */
    val hasNotificationSignal: Boolean = false,
    /** 이보다 짧으면 수면으로 보지 않는다. */
    val minSleepMs: Long = 3 * HOUR,
    /** 수면 조각으로 인정하는 최소 무사용 시간. */
    val minFragmentMs: Long = 30 * MINUTE,
    /** 맨 앞/뒤 조각은 이보다 길어야 수면에 포함 (잠들기 전·기상 후 잠깐 폰을 안 본 것과 구분). */
    val minEdgeFragmentMs: Long = 90 * MINUTE,
    /** 수면 조각 사이에 폰을 쓴 시간이 이 이하면 "잠깐 깸"으로 보고 같은 밤으로 묶는다. */
    val briefInterruptionMs: Long = 20 * MINUTE,
    /** 한밤중에 오래 깨어 있던 경우(불면)도 이 이하면 같은 밤으로 묶는다. 저녁에 시작한 조각에는 적용하지 않는다. */
    val maxInterruptionMs: Long = 90 * MINUTE,
    /** 이 시각 전(정오~)에 시작한 무사용 구간은 "저녁에 폰을 안 본 시간"일 가능성이 높다. */
    val eveningEndHour: Int = 22,
    /** 같은 날 후보가 여럿이면 이 시간대(coreNightStartHour~coreNightEndHour)와 많이 겹치는 쪽을 고른다. */
    val coreNightStartHour: Int = 22,
    val coreNightEndHour: Int = 9,
    /** 이미 이만큼 잤다면 "충분히 잔 뒤"로 보고, 그 뒤에 폰을 쓰면 기상으로 판단한다. */
    val mainSleepMs: Long = 5 * HOUR,
    /** 충분히 잔 뒤 아침(morningStartHour 이후)에는 이 이하로 잠깐 확인한 경우만 "다시 잠듦"으로 이어 붙인다. */
    val lateInterruptionMs: Long = 5 * MINUTE,
    val morningStartHour: Int = 6,
    /** 이보다 길면 수면이 아니라 폰을 두고 나간 것으로 보고 버린다. */
    val maxSleepMs: Long = 16 * HOUR,
    /** 화면 켜짐 시각 기준, 이 범위 안에 알림/충전 이벤트가 있으면 그것이 원인이다. */
    val correlationBeforeMs: Long = 10 * SECOND,
    val correlationAfterMs: Long = 3 * SECOND,
    /** 잠금 없는 기기에서 알림과 겹쳤더라도 이보다 오래 켜져 있으면 사용자가 쓴 것으로 본다. */
    val unsecuredPassiveMaxMs: Long = 3 * MINUTE,
    /** 잠금 없는 기기 + 알림 신호 없음: 이보다 오래 켜져 있어야 사용으로 본다. */
    val unsecuredActiveMinMs: Long = 30 * SECOND,
    /** true 면 밤 시간대(nightStartHour~다음날 nightEndHour)에 2/3 이상 걸친 것만 수면으로 인정. */
    val nightOnly: Boolean = true,
    val nightStartHour: Int = 21,
    val nightEndHour: Int = 12,
    /** 수면 중간 시각이 이 시각 이후면 다음 날짜의 밤으로 분류한다. */
    val dayBoundaryHour: Int = 18,
) {
    companion object {
        const val SECOND = 1000L
        const val MINUTE = 60 * SECOND
        const val HOUR = 60 * MINUTE
    }
}
