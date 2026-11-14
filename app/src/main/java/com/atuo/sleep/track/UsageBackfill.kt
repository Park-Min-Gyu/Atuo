package com.atuo.sleep.track

import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import com.atuo.sleep.core.EventType
import com.atuo.sleep.core.PhoneEvent
import com.atuo.sleep.data.EventStore
import com.atuo.sleep.data.Permissions

/**
 * 추적 서비스가 꺼져 있던 구간을 시스템 사용 기록(사용 기록 접근 권한)으로 복원한다.
 * SCREEN_INTERACTIVE 는 알림 미리보기(ambient)를 제외한 "완전히 켜짐"만,
 * KEYGUARD_HIDDEN 은 사용자가 잠금을 푼 경우만 기록되므로 같은 판정 규칙을 그대로 쓸 수 있다.
 */
object UsageBackfill {
    private const val MAX_RANGE = 7L * 24 * 60 * 60 * 1000

    /** @return 복원에 성공했으면 true (권한이 없거나 지원하지 않으면 false) */
    fun fill(context: Context, from: Long, to: Long): Boolean {
        if (Build.VERSION.SDK_INT < 28 || to <= from || !Permissions.usageAccess(context)) return false
        return try {
            val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val events = usm.queryEvents(maxOf(from, to - MAX_RANGE), to) ?: return false
            val out = ArrayList<PhoneEvent>()
            val e = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(e)
                val type = when (e.eventType) {
                    UsageEvents.Event.SCREEN_INTERACTIVE -> EventType.SCREEN_ON
                    UsageEvents.Event.SCREEN_NON_INTERACTIVE -> EventType.SCREEN_OFF
                    UsageEvents.Event.KEYGUARD_HIDDEN -> EventType.USER_UNLOCK
                    26 /* DEVICE_SHUTDOWN (API 29) */ -> EventType.SHUTDOWN
                    else -> null
                }
                if (type != null) out += PhoneEvent(e.timeStamp, type)
            }
            EventStore.get(context).addAll(out)
            true
        } catch (_: Exception) {
            false
        }
    }
}
