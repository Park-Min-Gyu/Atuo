package com.atuo.sleep.data

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.atuo.sleep.core.EventType
import com.atuo.sleep.core.PhoneEvent

/** 이벤트(시각 + 종류)만 저장하는 로컬 DB. 기기 밖으로 나가지 않는다. */
class EventStore private constructor(context: Context) :
    SQLiteOpenHelper(context, "events.db", null, 1) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, time INTEGER NOT NULL, type INTEGER NOT NULL)")
        db.execSQL("CREATE INDEX idx_events_time ON events(time)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit

    @Synchronized
    fun add(type: EventType, time: Long = System.currentTimeMillis()) {
        writableDatabase.insert("events", null, values(type, time))
    }

    @Synchronized
    fun addAll(events: List<PhoneEvent>) {
        if (events.isEmpty()) return
        val db = writableDatabase
        db.beginTransaction()
        try {
            for (e in events) db.insert("events", null, values(e.type, e.time))
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    @Synchronized
    fun since(from: Long): List<PhoneEvent> {
        val out = ArrayList<PhoneEvent>()
        readableDatabase.rawQuery(
            "SELECT time, type FROM events WHERE time >= ? ORDER BY time, id", arrayOf(from.toString())
        ).use { c ->
            while (c.moveToNext()) {
                val type = EventType.fromCode(c.getInt(1)) ?: continue
                out += PhoneEvent(c.getLong(0), type)
            }
        }
        return out
    }

    @Synchronized
    fun lastTime(): Long? =
        readableDatabase.rawQuery("SELECT MAX(time) FROM events", null).use { c ->
            if (c.moveToFirst() && !c.isNull(0)) c.getLong(0) else null
        }

    @Synchronized
    fun prune(before: Long) {
        writableDatabase.delete("events", "time < ?", arrayOf(before.toString()))
    }

    @Synchronized
    fun clear() {
        writableDatabase.delete("events", null, null)
    }

    private fun values(type: EventType, time: Long) = ContentValues().apply {
        put("time", time)
        put("type", type.code)
    }

    companion object {
        @Volatile
        private var instance: EventStore? = null

        fun get(context: Context): EventStore =
            instance ?: synchronized(this) {
                instance ?: EventStore(context.applicationContext).also { instance = it }
            }
    }
}
