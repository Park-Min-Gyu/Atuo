package com.atuo.sleep.data

import android.content.Context
import android.content.SharedPreferences

class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.applicationContext.getSharedPreferences("atuo", Context.MODE_PRIVATE)

    var trackingEnabled: Boolean
        get() = sp.getBoolean("tracking", true)
        set(v) = sp.edit().putBoolean("tracking", v).apply()

    /** 목표 수면 시간(분). */
    var goalMinutes: Int
        get() = sp.getInt("goal", 450)
        set(v) = sp.edit().putInt("goal", v).apply()

    /** 밤 시간대 수면만 인정. 야간 근무자는 끈다. */
    var nightOnly: Boolean
        get() = sp.getBoolean("nightOnly", true)
        set(v) = sp.edit().putBoolean("nightOnly", v).apply()

    var demo: Boolean
        get() = sp.getBoolean("demo", false)
        set(v) = sp.edit().putBoolean("demo", v).apply()

    var askedNotificationPermission: Boolean
        get() = sp.getBoolean("askedNotif", false)
        set(v) = sp.edit().putBoolean("askedNotif", v).apply()

    /** 마지막 주간 리포트 알림 시각. 처음 읽을 때 설치 시각으로 초기화된다. */
    var lastReportAt: Long
        get() {
            val v = sp.getLong("lastReport", 0L)
            if (v != 0L) return v
            val now = System.currentTimeMillis()
            sp.edit().putLong("lastReport", now).apply()
            return now
        }
        set(v) = sp.edit().putLong("lastReport", v).apply()
}
