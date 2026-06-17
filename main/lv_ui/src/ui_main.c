/**
 * @file ui_main.c
 * @brief LVGL 鏍稿績瑙嗗浘涓庤瑙夌敓鍛藉懆鏈熷紩鎿?
 *
 * @architecture
 * 鏈枃浠惰礋璐ｆ暣涓瀬瀹㈢數瀛愮浉鍐岀殑 UI 娓叉煋鐢熷懡鍛ㄦ湡锛屽寘鎷細
 * 1. 璺ㄥ睆鍙岀紦鍐茶瑙夋灦鏋勶細鎼浇 LVGL 8.x锛屾敮鎸佸钩婊戠殑 Crossfade 娓愬垏涓庡姩鎬佺収鐗囨覆鏌撱€?
 * 2. 澶氱嚎绋嬭祫婧愪繚鎶わ細浣跨敤 FreeRTOS 浜掓枼閲忎弗鏍间繚鎶ゆ墍鏈夌殑灞忓箷 API 娓叉煋璋冪敤锛岄槻姝?Core 0 鐨勫璁句笌缃戠粶娴佷簤鎶㈠鑷存€荤嚎宕╂簝銆?
 * 3. 鍔ㄦ€佺姸鎬佹爮缁勪欢锛氬寘鍚紶鎰熷櫒椹卞姩鐨勬暟鎹帹閫佷笌鐘舵€佹満鍒锋柊锛堢綉缁滅姸鎬併€佹俯搴︾瓑锛夈€?
 */
#include "ui_main.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lv_voice_assistant.h"
#include "lvgl.h"
#include "photo_client.h"
#include "ui_icons.h"
#include "ui_png_images.h"
#include "waveshare_rgb_lcd_port.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

static const char *TAG = "ui_main";

extern const lv_font_t lv_font_cjk_16;
extern const lv_font_t lv_font_cjk_20;
extern const lv_font_t lv_font_montserrat_14;
extern const lv_font_t lv_font_montserrat_20;
extern const lv_font_t lv_font_montserrat_28;

static lv_font_t ui_font_primary_safe; // 椤舵爮灏忓瓧鍙蜂腑鑻辨枃娣峰悎瀛楀簱
static lv_font_t ui_font_large_safe;   // 搴曟爮澶у瓧鍙蜂腑鑻辨枃娣峰悎瀛楀簱
static lv_font_t ui_font_caption_safe;
static lv_font_t ui_font_info_safe;
static void safe_cleanup_loading(void);

#define UI_FONT_PRIMARY (&ui_font_primary_safe) /* 椤舵爮鍏ㄥ眬浣跨敤 */
#define UI_FONT_LARGE (&ui_font_large_safe)     /* 搴曟爮鍏ㄥ眬浣跨敤 */
#define UI_FONT_CAPTION (&ui_font_caption_safe)
#define UI_FONT_INFO (&ui_font_info_safe)

// Active display stream size: 800x480 landscape.
#define LCD_W 800
#define LCD_H 480

static bool ui_is_portrait_mode(void) {
  lv_disp_t *disp = lv_disp_get_default();
  return disp && lv_disp_get_rotation(disp) == LV_DISP_ROT_90;
}

/* 鈹€鈹€ 浜掓枼閿佷笌浼犳劅鍣ㄧ紦瀛?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
/**
 * @brief UI 娓叉煋浜掓枼閿佸紩鐢ㄧ殑鐘舵€佺紦瀛?
 *
 * 娑夊強 FreeRTOS 浠诲姟闂撮€氫俊鏈哄埗銆傝儗鏅綉缁滀换鍔¤繍琛屽湪 Core 0锛岃€?LVGL 娓叉煋
 * 杩愯鍦?Core 1锛屽洜姝よ闂?LVGL 瀵硅薄鎴栧叡浜姸鎬佸繀椤绘寔閿侊紝闃叉鏁版嵁绔炰簤銆?
 */
static float latest_temp = 0.0f;  /* 鏈€鏂伴噰鏍锋俯搴?*/
static float latest_hum = 0.0f;   /* 鏈€鏂伴噰鏍锋箍搴?*/
static lv_obj_t *img_temp = NULL; /* Layer 0.5锛氱敤浜?Crossfade 鐨勪复鏃惰鐩栧浘 */

/* 鈹€鈹€ LVGL UI 鍩虹鎺т欢鍙ユ焺寮曠敤 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
static lv_obj_t *img_main;   /* Layer 0锛氬叏灞忕収鐗囪儗鏅浘鍍忔帶浠?*/
static lv_obj_t *top_bar;    /* Layer 1锛氶《閮ㄧ姸鎬佹爮瀹瑰櫒锛堟敮鎸佹噿鍔犺浇闅忓浘鏄剧幇锛?*/
static lv_obj_t *time_label; /* Layer 1锛氶《閮ㄧ姸鎬佹爮 - NTP 鏃堕棿鏃ユ湡鏄剧ず鏂囨湰 */
static lv_obj_t *temp_label; /* Layer 1锛氶《閮ㄧ姸鎬佹爮 - DHT20 瀹炴椂娓╁害鏄剧ず鏂囨湰 */
static lv_obj_t *hum_label;  /* Layer 1锛氶《閮ㄧ姸鎬佹爮 - DHT20 瀹炴椂婀垮害鏄剧ず鏂囨湰 */
static lv_obj_t
    *caption_container; /* Layer 2锛氬簳閮ㄥ崐閫忔槑鍦嗚鏂囨瀹瑰櫒锛堟敮鎸佺┖鐘舵€侀殣钘忥級 */
static lv_obj_t *caption_text_row;
static lv_obj_t
    *caption_label; /* Layer 2锛氬簳閮ㄥ鍣?- AI 澶фā鍨嬬浉鍐屾枃妗堟粴鍔ㄦ枃鏈?*/
static lv_obj_t *caption_comma_label;
static lv_obj_t *caption_tail_label;
static lv_obj_t *caption_period_dot;
static lv_obj_t *caption_info_row;
static lv_obj_t *photo_date_label;
static lv_obj_t *photo_sep_label;
static lv_obj_t
    *photo_info_label; /* Layer 2锛氬簳閮ㄥ鍣?- 鐓х墖鎹曡幏鏃堕棿涓庝笂浼犲湴鏂囨湰 */
static lv_obj_t *photo_img = NULL; /* 搴曢儴鐩告満鎸囩ず鍥炬爣 */
static lv_obj_t *upload_banner; /* Layer 3锛氶《閮ㄦ祦寮忓疄鏃剁収鐗囦笂浼犱俊鎭€氱煡妯箙 */
static lv_obj_t
    *aroma_leds[3]; /* Layer 1锛氶《閮ㄧ姸鎬佹爮 - 棣欒柊/闆惧寲鍚勯€氶亾鎸囩ず鐏泦 */
static lv_obj_t *history_popup = NULL; /* Layer 4锛氬叏灞忓巻鍙茶褰曡鎯呭脊绐楀鍣?*/
static lv_obj_t *loading_label;        
static lv_timer_t *loading_timer;      

/**
 * @brief LVGL 鍔ㄦ€佸浘鍍忔牸寮忔弿杩扮
 *
 * 澹版槑涓?static 鍏ㄥ眬缁撴瀯浣擄紝浠ョ‘淇濆湪鏁翠釜鏄剧ず娓叉煋鍛ㄦ湡鍐?
 * 浼犵粰 lv_img_set_src 鐨勫浘鍍忓厓鏁版嵁鍦ㄥ唴瀛樹腑鐗╃悊甯搁┗涓旀湁鏁堛€?
 */
static lv_img_dsc_t current_img_dsc;

/* 鍘嗗彶璁板綍 */
#define MAX_HISTORY 100

typedef struct {
  char path[128];
  char date[32];
  char caption[256];
} history_entry_t;

static history_entry_t history[MAX_HISTORY];
static int history_count = 0;

static size_t ui_utf8_char_count(const char *s) {
  if (!s) {
    return 0;
  }

  size_t count = 0;
  const unsigned char *p = (const unsigned char *)s;
  while (*p) {
    if ((*p & 0xC0) != 0x80) {
      count++;
    }
    p++;
  }
  return count;
}

static bool ui_take_fullwidth_punctuation(const char **src, char *out) {
  const unsigned char *p = (const unsigned char *)*src;
  if (!p[0] || !p[1] || !p[2]) {
    return false;
  }
  if (p[0] == 0xEF && p[1] == 0xBC) {
    switch (p[2]) {
    case 0x8C: *out = ','; break;
    case 0x81: *out = '!'; break;
    case 0x9F: *out = '?'; break;
    case 0x9A: *out = ':'; break;
    case 0x9B: *out = ';'; break;
    default: return false;
    }
    *src += 3;
    return true;
  }
  if (p[0] == 0xE3 && p[1] == 0x80) {
    switch (p[2]) {
    case 0x81: *out = ','; break;
    default: return false;
    }
    *src += 3;
    return true;
  }
  return false;
}

static bool ui_copy_caption_for_display(char *dst, size_t dst_size,
                                        const char *src) {
  if (!dst || dst_size == 0) {
    return false;
  }
  dst[0] = '\0';
  if (!src) {
    return false;
  }

  size_t used = 0;
  while (*src && used + 1 < dst_size) {
    char replacement = '\0';
    if (ui_take_fullwidth_punctuation(&src, &replacement)) {
      if (replacement == ',' && used + 3 < dst_size) {
        dst[used++] = (char)0xEF;
        dst[used++] = (char)0xBC;
        dst[used++] = (char)0x8C;
      } else if (used + 1 < dst_size) {
        dst[used++] = replacement;
      }
      continue;
    }
    if (*src == ',' && used + 3 < dst_size) {
      dst[used++] = (char)0xEF;
      dst[used++] = (char)0xBC;
      dst[used++] = (char)0x8C;
      src++;
      continue;
    }
    if (*src == '.' && used + 3 < dst_size) {
      dst[used++] = (char)0xE3;
      dst[used++] = (char)0x80;
      dst[used++] = (char)0x82;
      src++;
      continue;
    }
    dst[used++] = *src++;
  }
  dst[used] = '\0';

  while (used > 0 && (dst[used - 1] == ' ' || dst[used - 1] == '\r' ||
                      dst[used - 1] == '\n' || dst[used - 1] == '\t')) {
    dst[--used] = '\0';
  }

  return false;
}

static bool ui_split_caption_comma(char *text, char **tail) {
  if (!text || !tail) {
    return false;
  }

  *tail = NULL;
  char *comma = strchr(text, ',');
  if (!comma) {
    return false;
  }

  *comma = '\0';
  char *right = comma + 1;
  while (*right == ' ') {
    right++;
  }

  if (*right == '\0') {
    return false;
  }

  *tail = right;
  return true;
}

static void ui_update_caption_layout(const char *caption, bool is_portrait) {
  if (!caption_container) {
    return;
  }

  size_t chars = ui_utf8_char_count(caption);
  bool compact = chars <= (is_portrait ? 16 : 28);
  lv_coord_t caption_h = compact ? 52 : 86;
  lv_coord_t info_h = 34;
  lv_coord_t caption_label_h = compact ? 36 : 70;
  lv_coord_t height = caption_h + info_h + (compact ? 20 : 26);

  lv_obj_set_size(caption_container, is_portrait ? lv_pct(92) : lv_pct(96),
                  height);
  lv_obj_align(caption_container, LV_ALIGN_BOTTOM_MID, 0,
               is_portrait ? -12 : -8);
  lv_obj_set_style_pad_top(caption_container, compact ? 12 : 14, 0);
  lv_obj_set_style_pad_bottom(caption_container, compact ? 8 : 8, 0);
  lv_obj_set_style_pad_row(caption_container, compact ? 4 : 5, 0);
  lv_obj_set_flex_align(caption_container,
                        compact ? LV_FLEX_ALIGN_CENTER : LV_FLEX_ALIGN_START,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

  if (caption_text_row) {
    lv_obj_set_size(caption_text_row, lv_pct(100), caption_h);
    lv_obj_set_flex_align(caption_text_row, LV_FLEX_ALIGN_CENTER,
                          LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  }
  if (caption_label) {
    lv_obj_set_height(caption_label, caption_label_h);
    lv_obj_set_width(caption_label, compact ? LV_SIZE_CONTENT : lv_pct(94));
    lv_label_set_long_mode(caption_label,
                           compact ? LV_LABEL_LONG_CLIP : LV_LABEL_LONG_WRAP);
    lv_obj_set_style_pad_top(caption_label, compact ? 4 : 5, 0);
    lv_obj_set_style_pad_bottom(caption_label, compact ? 2 : 3, 0);
    lv_obj_set_style_text_line_space(caption_label, compact ? 3 : 5, 0);
  }
  if (caption_comma_label) {
    lv_obj_set_height(caption_comma_label, caption_label_h);
    lv_obj_set_width(caption_comma_label, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_top(caption_comma_label, compact ? 4 : 5, 0);
    lv_obj_set_style_pad_bottom(caption_comma_label, compact ? 2 : 3, 0);
    lv_obj_set_style_translate_y(caption_comma_label, -2, 0);
  }
  if (caption_tail_label) {
    lv_obj_set_height(caption_tail_label, caption_label_h);
    lv_obj_set_width(caption_tail_label, compact ? LV_SIZE_CONTENT : lv_pct(94));
    lv_label_set_long_mode(caption_tail_label,
                           compact ? LV_LABEL_LONG_CLIP : LV_LABEL_LONG_WRAP);
    lv_obj_set_style_pad_top(caption_tail_label, compact ? 4 : 5, 0);
    lv_obj_set_style_pad_bottom(caption_tail_label, compact ? 2 : 3, 0);
    lv_obj_set_style_text_line_space(caption_tail_label, compact ? 3 : 5, 0);
  }
  if (caption_info_row) {
    lv_obj_set_size(caption_info_row, LV_SIZE_CONTENT, info_h);
    lv_obj_set_style_pad_top(caption_info_row, 0, 0);
    lv_obj_set_style_pad_bottom(caption_info_row, 0, 0);
    lv_obj_set_style_pad_column(caption_info_row, 6, 0);
  }
  if (photo_info_label) {
    lv_obj_set_height(photo_info_label, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_top(photo_info_label, 0, 0);
    lv_obj_set_style_pad_bottom(photo_info_label, 0, 0);
    lv_obj_set_style_translate_y(photo_info_label, 0, 0);
  }
  if (photo_date_label) {
    lv_obj_set_height(photo_date_label, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_all(photo_date_label, 0, 0);
    lv_obj_set_style_translate_y(photo_date_label, 0, 0);
  }
  if (photo_sep_label) {
    lv_obj_set_height(photo_sep_label, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_all(photo_sep_label, 0, 0);
    lv_obj_set_style_translate_y(photo_sep_label, 0, 0);
  }
}

/**
 * @brief 璁板綍涓€鏉″巻鍙茬収鐗囦俊鎭?
 *
 * 鍦ㄥ绾跨▼鐜涓嬬敱澶栭儴浠诲姟璋冪敤銆傞渶閰嶅悎浜掓枼閿佷繚璇佸畨鍏ㄣ€?
 * 灏嗕紶鍏ョ殑璺緞銆佹棩鏈熴€佹弿杩颁俊鎭鍒惰繘鍘嗗彶璁板綍闃熷垪涓€?
 *
 * @param path    鐓х墖鍦?Flash 鎴?SPIFFS 涓殑鐗╃悊瀛樺偍璺緞
 * @param date    鐓х墖鍚屾鏃ユ湡鏂囨湰
 * @param caption 鐓х墖瀵瑰簲鐨?AI 鏂囨
 * @return void
 */
void ui_add_history(const char *path, const char *date, const char *caption) {
  if (history_count < MAX_HISTORY) {
    strncpy(history[history_count].path, path,
            sizeof(history[history_count].path) - 1);
    strncpy(history[history_count].date, date,
            sizeof(history[history_count].date) - 1);
    strncpy(history[history_count].caption, caption,
            sizeof(history[history_count].caption) - 1);
    history_count++;
  }
}

/* 鈹€鈹€ 鍐呴儴鍥剧墖鍒锋柊鏈哄埗 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
/**
 * @brief 鍦ㄥ凡鎸侀攣鐘舵€佷笅鏇存柊鐓х墖
 *
 * 鍐呴儴杈呭姪鍑芥暟锛屽閮ㄨ皟鐢ㄨ€呭繀椤荤‘淇濆凡鑾峰彇 LVGL 浜掓枼閿併€?
 *
 * @param data    鍥惧儚鏁版嵁鎸囬拡
 * @param caption 鍥惧儚瀵瑰簲鏂囨
 * @param city    鍥惧儚鍩庡競淇℃伅
 * @param date    鍥惧儚鏃ユ湡淇℃伅
 * @return void
 */
static void _set_photo_locked(uint8_t *data, const char *caption,
                              const char *city, const char *date) {
  if (data == NULL) {
    ESP_LOGE(TAG, "photo data is NULL, skip display");
    return;
  }
  memset(&current_img_dsc, 0, sizeof(current_img_dsc));
  bool is_portrait = ui_is_portrait_mode();
  current_img_dsc.header.cf = LV_IMG_CF_TRUE_COLOR;
  current_img_dsc.header.w = is_portrait ? LCD_H : LCD_W;
  current_img_dsc.header.h = is_portrait ? LCD_W : LCD_H;
  current_img_dsc.data_size = LCD_W * LCD_H * 2;
  current_img_dsc.data = data;

  if (img_main) {
    lv_img_set_src(img_main, &current_img_dsc);
  }
  if (top_bar) {
    lv_obj_clear_flag(top_bar, LV_OBJ_FLAG_HIDDEN);
  }
  if (caption_container) {
    lv_obj_clear_flag(caption_container, LV_OBJ_FLAG_HIDDEN);
  }

  safe_cleanup_loading();

  if (caption_label && caption) {
    char display_caption[256];
    bool has_period =
        ui_copy_caption_for_display(display_caption, sizeof(display_caption),
                                    caption);
    char layout_caption[256];
    strncpy(layout_caption, display_caption, sizeof(layout_caption) - 1);
    layout_caption[sizeof(layout_caption) - 1] = '\0';

    char *caption_tail = NULL;
    bool compact_caption =
        ui_utf8_char_count(layout_caption) <= (is_portrait ? 16 : 28);
    bool has_comma =
        compact_caption && ui_split_caption_comma(display_caption, &caption_tail);

    lv_label_set_text(caption_label, display_caption);
    if (caption_comma_label) {
      if (has_comma) {
        lv_obj_clear_flag(caption_comma_label, LV_OBJ_FLAG_HIDDEN);
      } else {
        lv_obj_add_flag(caption_comma_label, LV_OBJ_FLAG_HIDDEN);
      }
    }
    if (caption_tail_label) {
      if (has_comma && caption_tail) {
        lv_label_set_text(caption_tail_label, caption_tail);
        lv_obj_clear_flag(caption_tail_label, LV_OBJ_FLAG_HIDDEN);
      } else {
        lv_label_set_text(caption_tail_label, "");
        lv_obj_add_flag(caption_tail_label, LV_OBJ_FLAG_HIDDEN);
      }
    }
    if (caption_period_dot) {
      if (has_period) {
        lv_obj_clear_flag(caption_period_dot, LV_OBJ_FLAG_HIDDEN);
      } else {
        lv_obj_add_flag(caption_period_dot, LV_OBJ_FLAG_HIDDEN);
      }
    }
    ui_update_caption_layout(layout_caption, is_portrait);
  }
  if (photo_date_label && date) {
    // 1.
    
    // '-'
    char cleaned_date[64];
    strncpy(cleaned_date, date, sizeof(cleaned_date) - 1);
    cleaned_date[sizeof(cleaned_date) - 1] = '\0';
    for (int i = 0; cleaned_date[i] != '\0'; i++) {
      if (cleaned_date[i] == '\'' || cleaned_date[i] == '.' ||
          cleaned_date[i] == '/') {
        cleaned_date[i] = '-';
      }
    }

    // 2. 妫€鏌ュ煄甯?鍦扮偣鍚堟硶鎬э紙闃?portrait 绛夐潪鍚堟硶鏂规鏁版嵁锛?
    bool has_valid_city = false;
    if (city && strlen(city) > 0 && strcmp(city, "portrait") != 0 &&
        strcmp(city, "unknown") != 0) {
      has_valid_city = true;
    }

    // 3. 鏅鸿兘鑱斿姩鐩告満鎸囩ず鍥炬爣锛屾嫾鎺ヨ嚜閫傚簲鏄剧ず淇℃伅
    if (photo_img) {
      lv_obj_clear_flag(photo_img, LV_OBJ_FLAG_HIDDEN); // 濮嬬粓灞曠幇鐩告満灏忓浘鏍?
    }

    lv_label_set_text(photo_date_label, cleaned_date);
    if (has_valid_city) {
      if (photo_sep_label) {
        lv_obj_clear_flag(photo_sep_label, LV_OBJ_FLAG_HIDDEN);
      }
      if (photo_info_label) {
        lv_obj_clear_flag(photo_info_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(photo_info_label, city);
      }
    } else {
      if (photo_sep_label) {
        lv_obj_add_flag(photo_sep_label, LV_OBJ_FLAG_HIDDEN);
      }
      if (photo_info_label) {
        lv_obj_add_flag(photo_info_label, LV_OBJ_FLAG_HIDDEN);
        lv_label_set_text(photo_info_label, "");
      }
    }
  }
}

/* 鈹€鈹€ 缃戠粶灞備笌娓叉煋灞備氦鎺?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
/**
 * @brief 灏嗕笅杞界殑鐓х墖鏁版嵁鎻愪氦缁?UI 娓叉煋
 *
 * 鐢?photo_client 浠诲姟璋冪敤锛岃鍑芥暟鍐呴儴浼氬皢鏁版嵁浜ゆ帴缁?LVGL锛?
 * 鎵ц鏃跺皢閿佸畾 LVGL 浜掓枼閲忥紝瀹炵幇浠诲姟闂寸殑瀹夊叏浜や簰銆?
 *
 * @param rgb565_data 鍥惧儚 RGB565 鏁版嵁鎸囬拡
 * @param len         鐐归樀鏁版嵁闀垮害
 * @param caption     AI 鏂囨
 * @param city        鐓х墖鍩庡競淇℃伅
 * @param date        鐓х墖鎷嶆憚鏃ユ湡
 * @return void
 */
void ui_set_photo_data(uint8_t *rgb565_data, size_t len, const char *caption,
                       const char *city, const char *date) {
  _set_photo_locked(rgb565_data, caption, city, date);
}

/* 鈹€鈹€ 姹囩紪娈电鍙锋槧灏勫紩鐢?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
extern const uint8_t
    placeholder_rgb565_start[] asm("_binary_placeholder_rgb565_start");
extern const uint8_t
    placeholder_rgb565_end[] asm("_binary_placeholder_rgb565_end");

extern const uint8_t
    placeholder_landscape_rgb565_start[] asm("_binary_placeholder_landscape_rgb565_start");
extern const uint8_t
    placeholder_landscape_rgb565_end[] asm("_binary_placeholder_landscape_rgb565_end");

/**
 * @brief 鏄剧ず鐩稿唽鐨勭┖鐘舵€佸崰浣嶅浘
 *
 * 褰撹澶囨湭鑱旂綉鎴栫収鐗囧垪琛ㄤ负绌烘椂璋冪敤銆?
 * 鍗犱綅鍥句娇鐢ㄧ紪璇戞湡闂撮摼鎺ュ埌 Flash 涓殑闈欐€佹暟鎹紝閬垮厤杩愯鏃剁殑鍫嗗唴瀛樺紑閿€銆?
 *
 * @return void
 */
void ui_show_placeholder(void) {
  if (img_main) {
    bool is_portrait = ui_is_portrait_mode();
    
    memset(&current_img_dsc, 0, sizeof(current_img_dsc));
    current_img_dsc.header.always_zero = 0;
    current_img_dsc.header.w = is_portrait ? 480 : 800;
    current_img_dsc.header.h = is_portrait ? 800 : 480;
    current_img_dsc.header.cf = LV_IMG_CF_TRUE_COLOR;
    
    if (is_portrait) {
        current_img_dsc.data_size = (size_t)(placeholder_rgb565_end - placeholder_rgb565_start);
        current_img_dsc.data = placeholder_rgb565_start;
    } else {
        current_img_dsc.data_size = (size_t)(placeholder_landscape_rgb565_end - placeholder_landscape_rgb565_start);
        current_img_dsc.data = placeholder_landscape_rgb565_start;
    }

    lv_img_set_src(img_main, &current_img_dsc);
  }
  if (top_bar) {
    lv_obj_clear_flag(top_bar, LV_OBJ_FLAG_HIDDEN);
  }
  safe_cleanup_loading();
  if (caption_container) {
    lv_obj_add_flag(caption_container, LV_OBJ_FLAG_HIDDEN);
  }
  if (caption_label) {
    lv_label_set_text(caption_label, " ");
  }
  if (caption_comma_label) {
    lv_obj_add_flag(caption_comma_label, LV_OBJ_FLAG_HIDDEN);
  }
  if (caption_tail_label) {
    lv_label_set_text(caption_tail_label, " ");
    lv_obj_add_flag(caption_tail_label, LV_OBJ_FLAG_HIDDEN);
  }
  if (caption_period_dot) {
    lv_obj_add_flag(caption_period_dot, LV_OBJ_FLAG_HIDDEN);
  }
  if (photo_date_label) {
    lv_label_set_text(photo_date_label, " ");
  }
  if (photo_sep_label) {
    lv_obj_add_flag(photo_sep_label, LV_OBJ_FLAG_HIDDEN);
  }
  if (photo_info_label) {
    lv_obj_add_flag(photo_info_label, LV_OBJ_FLAG_HIDDEN);
    lv_label_set_text(photo_info_label, " ");
  }
}

/* 鈹€鈹€ 涓婁紶鐓х墖閫氱煡 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
/**
 * @brief 澶勭悊涓婁紶鐓х墖閫氱煡骞跺湪椤堕儴寮瑰嚭妯箙
 *
 * 鍒锋柊搴曞眰鑳屾櫙鍥撅紝骞跺姞杞藉甫鏈夋粴鍔ㄦ寚绀烘枃鏈殑閫氱煡妯箙銆?
 *
 * @param rgb565_data 鐓х墖鐨勫師濮嬬偣闃垫暟鎹寚閽?
 * @param len         鐓х墖鐐归樀澶у皬
 * @param message     涓婁紶鑰呴檮甯︾殑娑堟伅
 * @param uploader    涓婁紶鑰呮爣璇?
 * @return void
 */

/* 鈹€鈹€ 鐘舵€佹洿鏂版帴鍙?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
/**
 * @brief 鏇存柊椤堕儴鐘舵€佹爮鐨勬俯婀垮害淇℃伅
 *
 * 琚璁惧悗鍙颁换鍔″惊鐜皟鐢ㄣ€傛鍑芥暟鍦ㄦ搷浣?LVGL 鎺т欢鍓嶉渶纭繚宸茶幏鍙?LVGL 浜掓枼閿併€?
 *
 * @param temp 鏈湴浼犳劅鍣ㄩ噰鏍风殑娓╁害
 * @param hum  鏈湴浼犳劅鍣ㄩ噰鏍风殑婀垮害
 * @return void
 */
void ui_update_weather(float temp, float hum) {
  latest_temp = temp;
  latest_hum = hum;
  if (temp_label) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%.1fC", temp);
    lv_label_set_text(temp_label, buf);
  }
  if (hum_label) {
    char buf[16];
    snprintf(buf, sizeof(buf), "%.0f%%", hum);
    lv_label_set_text(hum_label, buf);
  }
}

/**
 * @brief 鏇存柊椤堕儴鐘舵€佹爮鐨?NTP 鏃堕棿
 *
 * 鐢?UI 鍐呴儴瀹氭椂鍣ㄥ懆鏈熸€у洖璋冿紝閫氳繃 localtime 妫€鏌ユ槸鍚﹀凡涓?SNTP 鍚屾銆?
 *
 * @return void
 */
void ui_update_time(void) {
  if (time_label) {
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);
    char buf[64];
    if (timeinfo.tm_year > (2000 - 1900)) { // 宸茬粡杩?SNTP 鏍″噯
      snprintf(buf, sizeof(buf), "%04d-%02d-%02d %02d:%02d",
               timeinfo.tm_year + 1900, timeinfo.tm_mon + 1, timeinfo.tm_mday,
               timeinfo.tm_hour, timeinfo.tm_min);
    } else { // 灏氭湭鏍″噯
      snprintf(buf, sizeof(buf), "----- -- - ----:--");
    }
    lv_label_set_text(time_label, buf);
  }
}

/**
 * @brief 鏇存柊棣欒柊閫氶亾鐨?UI 鐘舵€佹寚绀虹伅
 *
 * @param ch1 閫氶亾 1 鐘舵€?
 * @param ch2 閫氶亾 2 鐘舵€?
 * @param ch3 閫氶亾 3 鐘舵€?
 * @return void
 */
void ui_update_aroma_status(bool ch1, bool ch2, bool ch3) {
  bool status[3] = {ch1, ch2, ch3};
  for (int i = 0; i < 3; i++) {
    if (aroma_leds[i]) {
      if (status[i]) {
        lv_img_set_src(aroma_leds[i], &ui_img_led_green); // 3D缁胯壊鎵撳紑
      } else {
        lv_img_set_src(aroma_leds[i], &ui_img_led_red); // 3D绾㈣壊鍏抽棴
      }
    }
  }
}

/* 鍘嗗彶璁板綍寮圭獥 */
/**
 * @brief 鍏抽棴鍘嗗彶璁板綍寮圭獥鍥炶皟
 *
 * @param e LVGL 鐐瑰嚮浜嬩欢缁撴瀯浣撴寚閽?
 * @return void
 */
static void close_history_popup_cb(lv_event_t *e) {
  if (history_popup) {
    lv_obj_del(history_popup);
    history_popup = NULL;
  }
}

/**
 * @brief 鏄剧ず鍘嗗彶璁板綍寮圭獥
 *
 * 鍒涘缓鍏ㄥ睆鍗婇€忔槑閬僵涓庡眳涓殑鍐呭瀹瑰櫒锛屽姩鎬佹覆鏌撳巻鍙茶褰曞垪琛ㄣ€?
 *
 * @return void
 */
void ui_show_history_popup(void) {
  if (history_popup) {
    lv_obj_del(history_popup);
    history_popup = NULL;
  }

  /* 鍏ㄥ睆鍗婇€忔槑閬僵 */
  history_popup = lv_obj_create(lv_scr_act());
  lv_obj_set_size(history_popup, lv_pct(100), lv_pct(100));
  lv_obj_align(history_popup, LV_ALIGN_CENTER, 0, 0);
  lv_obj_set_style_bg_color(history_popup, lv_color_hex(0x000000), 0);
  lv_obj_set_style_bg_opa(history_popup, 200, 0);
  lv_obj_set_style_border_width(history_popup, 0, 0);

  /* 鍐呭瀹瑰櫒 */
  lv_obj_t *content = lv_obj_create(history_popup);
  lv_obj_set_size(content, lv_pct(88), lv_pct(82));
  lv_obj_align(content, LV_ALIGN_CENTER, 0, 0);
  lv_obj_set_style_bg_color(content, lv_color_hex(0x1A1A1A), 0);
  lv_obj_set_style_radius(content, 14, 0);
  lv_obj_set_style_border_width(content, 0, 0);

  lv_obj_t *title = lv_label_create(content);
  lv_label_set_text(title, "鍘嗗彶璁板綍");
  lv_obj_set_style_text_font(title, UI_FONT_PRIMARY, 0);
  lv_obj_set_style_text_color(title, lv_color_hex(0xFFFFFF), 0);
  lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 10);

  lv_obj_t *list = lv_obj_create(content);
  lv_obj_set_size(list, lv_pct(90), lv_pct(78));
  lv_obj_align(list, LV_ALIGN_CENTER, 0, 0);
  lv_obj_set_style_bg_color(list, lv_color_hex(0x222222), 0);
  lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_scroll_dir(list, LV_DIR_VER);

  if (history_count == 0) {
    lv_obj_t *empty = lv_label_create(list);
    lv_label_set_text(empty, "鏆傛棤鍘嗗彶璁板綍");
    lv_obj_set_style_text_color(empty, lv_color_hex(0x666666), 0);
    lv_obj_set_style_text_font(empty, UI_FONT_PRIMARY, 0);
    lv_obj_align(empty, LV_ALIGN_CENTER, 0, 0);
  } else {
    for (int i = history_count - 1; i >= 0; i--) {
      lv_obj_t *row = lv_obj_create(list);
      lv_obj_set_size(row, lv_pct(100), 44);
      lv_obj_set_style_bg_color(row, lv_color_hex(0x333333), 0);
      lv_obj_set_style_border_width(row, 0, 0);
      lv_obj_set_style_radius(row, 6, 0);
      lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);

      lv_obj_t *date_l = lv_label_create(row);
      lv_label_set_text(date_l, history[i].date);
      lv_obj_set_style_text_color(date_l, lv_color_hex(0xAAAAAA), 0);
      lv_obj_set_style_text_font(date_l, UI_FONT_PRIMARY, 0);
      lv_obj_set_width(date_l, 90);

      lv_obj_t *cap_l = lv_label_create(row);
      char short_cap[64];
      strncpy(short_cap, history[i].caption, sizeof(short_cap) - 1);
      short_cap[sizeof(short_cap) - 1] = '\0';
      lv_label_set_text(cap_l, short_cap);
      lv_obj_set_style_text_color(cap_l, lv_color_hex(0xFFFFFF), 0);
      lv_obj_set_style_text_font(cap_l, UI_FONT_PRIMARY, 0);
      lv_obj_set_flex_grow(cap_l, 1);
    }
  }

  /* 鍏抽棴鎸夐挳 */
  lv_obj_t *close_btn = lv_btn_create(content);
  lv_obj_set_size(close_btn, 70, 34);
  lv_obj_align(close_btn, LV_ALIGN_BOTTOM_MID, 0, -10);
  lv_obj_t *close_l = lv_label_create(close_btn);
  lv_label_set_text(close_l, "鍏抽棴");
  lv_obj_set_style_text_font(close_l, UI_FONT_PRIMARY, 0);
  lv_obj_center(close_l);
  lv_obj_add_event_cb(close_btn, close_history_popup_cb, LV_EVENT_CLICKED,
                      NULL);
}

/* UI 瀵艰埅鎺ュ彛 */
void ui_refresh(void) {}
void ui_image_next(void) {}
void ui_image_prev(void) {}

void ui_set_screen_rotation(bool is_portrait) {
  if (!lvgl_port_lock(100)) {
    return;
  }

  lv_disp_t *disp = lv_disp_get_default();
  if (disp) {
    lv_disp_set_rotation(disp,
                         is_portrait ? LV_DISP_ROT_90 : LV_DISP_ROT_NONE);
    lvgl_port_set_rotation(is_portrait ? 90 : 0);
  }

  if (img_main && current_img_dsc.data != NULL) {
    if (current_img_dsc.data == placeholder_rgb565_start ||
        current_img_dsc.data == placeholder_landscape_rgb565_start) {
      ui_show_placeholder();
    }
  }

  if (caption_container) {
    const char *caption_text =
        caption_label ? lv_label_get_text(caption_label) : "";
    ui_update_caption_layout(caption_text, is_portrait);
  }

  if (top_bar) {
    lv_obj_set_height(top_bar, is_portrait ? 30 : 34);
  }

  lvgl_port_unlock();
}
/* 寮€鏈哄姩鐢诲畾鏃跺櫒 */
static void loading_timer_cb(lv_timer_t *t) {
  static int dot_count = 0;
  dot_count = (dot_count % 3) + 1;
  char buf[32];

  if (dot_count == 1)
    snprintf(buf, sizeof(buf), "LOADING.");
  else if (dot_count == 2)
    snprintf(buf, sizeof(buf), "LOADING..");
  else
    snprintf(buf, sizeof(buf), "LOADING...");

  if (loading_label) {
    lvgl_port_lock(0);
    lv_label_set_text(loading_label, buf);
    lvgl_port_unlock();
  }
}
static void safe_cleanup_loading(void) {
  if (loading_timer) {
    lv_timer_pause(loading_timer);
    lv_timer_del(loading_timer);
    loading_timer = NULL;
  }
  if (loading_label) {
    lvgl_port_lock(0);
    lv_obj_del(loading_label);
    lvgl_port_unlock();
    loading_label = NULL;
  }
}

/* 涓荤晫闈㈠垵濮嬪寲 */
/**
 * @brief 鍒濆鍖?LVGL 涓荤晫闈㈠竷灞€
 *
 * 鎸夌収绔栧睆 480x800 鏋勫缓鏍稿績鍥惧眰锛氳儗鏅浘灞傘€侀《閮ㄧ姸鎬佹爮銆佸簳閮ㄦ枃妗堟绛夈€?
 *
 * @return void
 */
void ui_main_init(void) {
  /* 鎸傝浇涓枃瀛楀簱 */
  memcpy(&ui_font_primary_safe, &lv_font_montserrat_14, sizeof(lv_font_t));
  ui_font_primary_safe.fallback = &lv_font_cjk_16;

  memcpy(&ui_font_large_safe, &lv_font_montserrat_20, sizeof(lv_font_t));
  ui_font_large_safe.fallback = &lv_font_cjk_20;

  memcpy(&ui_font_caption_safe, &lv_font_montserrat_20, sizeof(lv_font_t));
  ui_font_caption_safe.fallback = &lv_font_cjk_20;

  memcpy(&ui_font_info_safe, &lv_font_cjk_16, sizeof(lv_font_t));
  ui_font_info_safe.fallback = &lv_font_montserrat_14;

  lv_obj_t *scr = lv_scr_act();
  lv_obj_set_style_bg_color(scr, lv_color_hex(0xFFFFFF),
                            0); /* 绾櫧鑳屾櫙涓庣‖浠跺惎鍔ㄧ壒鎬ф棤缂濊鎺?*/
  lv_obj_set_style_pad_all(scr, 0, 0);
  lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

  /* 鈹€鈹€ Layer 0锛堟渶搴曞眰锛夛細鍏ㄥ睆娌夋蹈鐓х墖鑳屾櫙锛堟秷闄ょ‖榛戣竟锛?鈹€鈹€ */
  img_main = lv_img_create(scr);
  lv_obj_set_size(img_main, lv_pct(100), lv_pct(100)); /* 閾烘弧鍏ㄥ睆 480x800 */
  lv_obj_align(img_main, LV_ALIGN_TOP_LEFT, 0, 0);     /* 璧峰浜庨《瑙?(0, 0) */
  lv_obj_set_style_bg_color(img_main, lv_color_hex(0x111111), 0);
  lv_obj_move_background(img_main);

  /* 鈹€鈹€ Layer 1锛氭偓娴崐閫忔槑椤堕儴淇℃伅鐩栨澘 鈹€鈹€ */
  top_bar = lv_obj_create(scr);
  lv_obj_add_flag(
      top_bar,
      LV_OBJ_FLAG_HIDDEN); /* 涓婄數鍒濇湡榛樿闅愯棌锛屽緟鍥剧墖灏辩华鍚庡啀闅忓浘鏄剧幇 */
  lv_obj_set_size(top_bar, lv_pct(100),
                  34); /* 楂樺害璁惧畾涓?34px锛屼繚璇佺姸鎬佹樉绀烘洿瀵屽懠鍚告劅涓庡绉?*/
  lv_obj_align(top_bar, LV_ALIGN_TOP_MID, 0, 0);
  lv_obj_set_style_bg_color(top_bar, lv_color_hex(0x1A1C1E), 0); /* 鏋佹殫鑹插簳 */
  lv_obj_set_style_bg_opa(top_bar, 100,
                          0); /* 绾?40% 鐨勯珮闆呭崐閫忔槑锛屾偓娴簬鐓х墖涔嬩笂 */
  lv_obj_set_style_border_width(top_bar, 0, 0);
  lv_obj_set_style_radius(top_bar, 0, 0);
  lv_obj_set_style_pad_left(top_bar, 12,
                            0); /* 宸﹀彸杈硅窛瀵圭О 12px锛屼笉绱ц创灞忓箷杈圭紭 */
  lv_obj_set_style_pad_right(top_bar, 12, 0);
  lv_obj_set_style_pad_top(top_bar, 0, 0); /* 涓婁笅 padding 瀵圭О娓呴浂 */
  lv_obj_set_style_pad_bottom(top_bar, 0, 0);
  lv_obj_clear_flag(top_bar, LV_OBJ_FLAG_SCROLLABLE);

  /* 璁剧疆 Flex 甯冨眬锛屼袱绔榻?*/
  lv_obj_set_flex_flow(top_bar, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(top_bar, LV_FLEX_ALIGN_SPACE_BETWEEN,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

  /* 宸﹀榻愬尯鍩燂細娓╂箍搴?*/
  lv_obj_t *weather_row = lv_obj_create(top_bar);
  lv_obj_set_size(weather_row, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_set_style_bg_opa(weather_row, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(weather_row, 0, 0);
  lv_obj_set_style_pad_all(weather_row, 0, 0);
  lv_obj_set_height(weather_row, 24); /* 楂樺害璁惧畾涓?24px */
  lv_obj_set_flex_flow(weather_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(weather_row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(weather_row, 4,
                              0); /* 缁熶竴璁剧疆 Flex 姘村钩鍒楅棿璺濅负 4px */

  lv_obj_t *temp_img = lv_img_create(weather_row);
  lv_img_set_src(temp_img, &ui_img_temp);

  temp_label = lv_label_create(weather_row);
  lv_label_set_text(temp_label, "--C");
  lv_obj_set_style_text_color(temp_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_text_font(temp_label, UI_FONT_PRIMARY, 0);

  lv_obj_t *hum_img = lv_img_create(weather_row);
  lv_img_set_src(hum_img, &ui_img_hum);

  hum_label = lv_label_create(weather_row);
  lv_label_set_text(hum_label, "--%");
  lv_obj_set_style_text_color(hum_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_text_font(hum_label, UI_FONT_PRIMARY, 0);

  /* 灞呬腑鍖哄煙锛氭椂闂存棩鏈?*/
  lv_obj_t *time_row = lv_obj_create(top_bar);
  lv_obj_set_size(time_row, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_set_style_bg_opa(time_row, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(time_row, 0, 0);
  lv_obj_set_style_pad_all(time_row, 0, 0);
  lv_obj_set_height(time_row, 24); /* 涓庢俯婀垮害琛屼繚鎸佷竴鑷?*/
  lv_obj_set_flex_flow(time_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(time_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(time_row, 5,
                              0); /* 缁熶竴璁剧疆 Flex 姘村钩闂磋窛涓?5px */

  lv_obj_t *time_img = lv_img_create(time_row);
  lv_img_set_src(time_img, &ui_img_calendar);

  time_label = lv_label_create(time_row);
  lv_label_set_text(time_label, "----.--.--  --:--");
  lv_obj_set_style_text_color(time_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_text_font(time_label, UI_FONT_PRIMARY, 0);

  /* 鍙冲榻愬尯鍩燂細棣欒柊鍜孡ED鎸囩ず鐏?*/
  lv_obj_t *aroma_row = lv_obj_create(top_bar);
  lv_obj_set_size(aroma_row, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
  lv_obj_set_style_bg_opa(aroma_row, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(aroma_row, 0, 0);
  lv_obj_set_style_pad_all(aroma_row, 0, 0);
  lv_obj_set_height(aroma_row, 24); /* 涓庡叾瀹冭瀵归綈 */
  lv_obj_set_flex_flow(aroma_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(aroma_row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_column(aroma_row, 4,
                              0); /* 缁熶竴璁剧疆 Flex 姘村钩闂磋窛涓?4px */

  lv_obj_t *aroma_img = lv_img_create(aroma_row);
  lv_img_set_src(aroma_img, &ui_img_fragrance);

  for (int i = 0; i < 3; i++) {
    aroma_leds[i] = lv_img_create(aroma_row);
    lv_img_set_src(aroma_leds[i], &ui_img_led_red); /* 榛樿 3D 绾㈣壊鍏抽棴 */
  }

  /* 鈹€鈹€ Layer 2锛氭矇娴告繁鑹插崐閫忔槑搴曟爮鍗＄墖锛堥攣姝讳负 55px 璐村簳涓嶅姞楂橈級 鈹€鈹€ */
  caption_container = lv_obj_create(scr);
  lv_obj_add_flag(caption_container,
                  LV_OBJ_FLAG_HIDDEN); /* 榛樿闅愯棌锛屽緟鍥剧墖灏辩华鍚庡啀闅忕収鐗囨樉鐜?*/
  lv_obj_set_size(caption_container, lv_pct(92), 104); /* 娴姩鍗＄墖鏍峰紡 */
  lv_obj_align(caption_container, LV_ALIGN_BOTTOM_MID, 0, -10); /* 鎶搴曢儴 */
  lv_obj_set_style_bg_color(caption_container, lv_color_hex(0x1A1C1E), 0);
  lv_obj_set_style_bg_opa(caption_container, 160, 0); /* 鏇翠笉閫忔槑鐨勫崱鐗?*/
  lv_obj_set_style_border_width(caption_container, 0, 0);
  lv_obj_set_style_radius(caption_container, 10, 0); /* 鍦嗚娴眰 */
  lv_obj_set_style_shadow_width(caption_container, 0, 0);
  lv_obj_set_style_pad_left(caption_container, 12, 0);
  lv_obj_set_style_pad_right(caption_container, 12, 0);
  lv_obj_set_style_pad_top(caption_container, 10, 0);
  lv_obj_set_style_pad_bottom(caption_container, 8, 0);
  lv_obj_clear_flag(caption_container, LV_OBJ_FLAG_SCROLLABLE);

  /* 绾靛悜 Flex 甯冨眬 */
  lv_obj_set_flex_flow(caption_container, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(caption_container, LV_FLEX_ALIGN_START,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
  lv_obj_set_style_pad_row(caption_container, 2, 0); /* 绋冲畾涓よ鏂囨涓庢棩鏈熻闂磋窛 */

  caption_text_row = lv_obj_create(caption_container);
  lv_obj_set_size(caption_text_row, lv_pct(100), 52);
  lv_obj_set_style_bg_opa(caption_text_row, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(caption_text_row, 0, 0);
  lv_obj_set_style_shadow_width(caption_text_row, 0, 0);
  lv_obj_set_style_pad_all(caption_text_row, 0, 0);
  lv_obj_clear_flag(caption_text_row, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_pad_column(caption_text_row, 3, 0);
  lv_obj_set_flex_flow(caption_text_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(caption_text_row, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

  /* 绗竴琛岋細AI澶фā鍨嬫枃妗堬紝闈欐€佹姌琛岋紝绾櫧鑹诧紝灞呬腑 */
  caption_label = lv_label_create(caption_text_row);
  lv_obj_set_width(caption_label, LV_SIZE_CONTENT);
  lv_obj_set_height(caption_label, 36);
  lv_label_set_text(caption_label, "");
  lv_label_set_long_mode(caption_label,
                         LV_LABEL_LONG_CLIP); /* 鑷€傚簲瀛椾綋鐪熷疄琛岄珮锛岄伩鍏嶉《绔鍒?*/
  lv_obj_set_style_text_color(
      caption_label, lv_color_hex(0xFFFFFF),
      0); /* 绾櫧鑹查珮浜紝淇濊瘉鍦ㄦ繁鑹插崐閫忔槑鐜荤拑搴曡壊涓婃瀬鍏堕啋鐩?*/
  lv_obj_set_style_text_font(caption_label, UI_FONT_CAPTION, 0);
  lv_obj_set_style_text_align(caption_label, LV_TEXT_ALIGN_CENTER,
                              0); 
  lv_obj_set_style_pad_left(caption_label, 0, 0);
  lv_obj_set_style_pad_top(caption_label, 4, 0);
  lv_obj_set_style_pad_bottom(caption_label, 2, 0);
  lv_obj_set_style_text_line_space(caption_label, 4, 0);

  caption_comma_label = lv_label_create(caption_text_row);
  lv_obj_set_width(caption_comma_label, LV_SIZE_CONTENT);
  lv_obj_set_height(caption_comma_label, 36);
  lv_label_set_text(caption_comma_label, ",");
  lv_label_set_long_mode(caption_comma_label, LV_LABEL_LONG_CLIP);
  lv_obj_set_style_text_color(caption_comma_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_text_font(caption_comma_label, UI_FONT_CAPTION, 0);
  lv_obj_set_style_pad_left(caption_comma_label, 0, 0);
  lv_obj_set_style_pad_right(caption_comma_label, 2, 0);
  lv_obj_set_style_pad_top(caption_comma_label, 4, 0);
  lv_obj_set_style_pad_bottom(caption_comma_label, 2, 0);
  lv_obj_set_style_translate_y(caption_comma_label, -2, 0);
  lv_obj_add_flag(caption_comma_label, LV_OBJ_FLAG_HIDDEN);

  caption_tail_label = lv_label_create(caption_text_row);
  lv_obj_set_width(caption_tail_label, LV_SIZE_CONTENT);
  lv_obj_set_height(caption_tail_label, 36);
  lv_label_set_text(caption_tail_label, "");
  lv_label_set_long_mode(caption_tail_label, LV_LABEL_LONG_CLIP);
  lv_obj_set_style_text_color(caption_tail_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_text_font(caption_tail_label, UI_FONT_CAPTION, 0);
  lv_obj_set_style_text_align(caption_tail_label, LV_TEXT_ALIGN_CENTER, 0);
  lv_obj_set_style_pad_left(caption_tail_label, 0, 0);
  lv_obj_set_style_pad_top(caption_tail_label, 4, 0);
  lv_obj_set_style_pad_bottom(caption_tail_label, 2, 0);
  lv_obj_set_style_text_line_space(caption_tail_label, 4, 0);
  lv_obj_add_flag(caption_tail_label, LV_OBJ_FLAG_HIDDEN);

  caption_period_dot = lv_obj_create(caption_text_row);
  lv_obj_set_size(caption_period_dot, 5, 5);
  lv_obj_set_style_radius(caption_period_dot, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_opa(caption_period_dot, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(caption_period_dot, 1, 0);
  lv_obj_set_style_border_color(caption_period_dot, lv_color_hex(0xFFFFFF), 0);
  lv_obj_set_style_pad_all(caption_period_dot, 0, 0);
  lv_obj_set_style_translate_y(caption_period_dot, 0, 0);
  lv_obj_add_flag(caption_period_dot, LV_OBJ_FLAG_HIDDEN);
  lv_obj_clear_flag(caption_period_dot, LV_OBJ_FLAG_SCROLLABLE);

  /* 绗簩琛岋細鐩告満淇℃伅琛?*/
  caption_info_row = lv_obj_create(caption_container);
  lv_obj_set_size(caption_info_row, LV_SIZE_CONTENT, 36);
  lv_obj_set_style_bg_opa(caption_info_row, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_width(caption_info_row, 0, 0);
  lv_obj_set_style_outline_width(caption_info_row, 0,
                                 0); /* 寮哄姏娓呴浂榛樿澶栬疆寤撲互绮夌榛戞 Bug */
  lv_obj_set_style_shadow_width(caption_info_row, 0,
                                0); /* 寮哄姏娓呴浂榛樿鎶曞奖浠ョ矇纰庨粦妗?Bug */
  lv_obj_set_style_pad_all(caption_info_row, 0, 0);
  lv_obj_set_style_pad_top(caption_info_row, 0, 0);
  lv_obj_set_style_pad_bottom(caption_info_row, 0, 0);
  lv_obj_set_style_pad_column(caption_info_row, 6, 0);
  lv_obj_set_flex_flow(caption_info_row, LV_FLEX_FLOW_ROW);
  lv_obj_set_flex_align(caption_info_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER,
                        LV_FLEX_ALIGN_CENTER);

  photo_img = lv_img_create(caption_info_row);
  lv_img_set_src(photo_img, &ui_img_camera);
  lv_obj_set_size(photo_img, 20, 20);
  
  lv_obj_set_style_img_recolor(photo_img, lv_color_hex(0x7F8C8D), 0);
  lv_obj_set_style_img_recolor_opa(photo_img, LV_OPA_COVER, 0);

  photo_date_label = lv_label_create(caption_info_row);
  lv_label_set_text(photo_date_label, "");
  lv_obj_set_height(photo_date_label, LV_SIZE_CONTENT);
  lv_obj_set_style_text_color(photo_date_label, lv_color_hex(0x7F8C8D), 0);
  lv_obj_set_style_text_font(photo_date_label, &lv_font_montserrat_14, 0);
  lv_obj_set_style_pad_all(photo_date_label, 0, 0);
  lv_obj_set_style_translate_y(photo_date_label, 0, 0);

  photo_sep_label = lv_label_create(caption_info_row);
  lv_label_set_text(photo_sep_label, " | ");
  lv_obj_set_height(photo_sep_label, LV_SIZE_CONTENT);
  lv_obj_set_style_text_color(photo_sep_label, lv_color_hex(0x7F8C8D), 0);
  lv_obj_set_style_text_font(photo_sep_label, &lv_font_montserrat_14, 0);
  lv_obj_set_style_pad_all(photo_sep_label, 0, 0);
  lv_obj_set_style_translate_y(photo_sep_label, 0, 0);
  lv_obj_add_flag(photo_sep_label, LV_OBJ_FLAG_HIDDEN);

  photo_info_label = lv_label_create(caption_info_row);
  lv_label_set_text(photo_info_label, "");
  lv_obj_set_height(photo_info_label, LV_SIZE_CONTENT);
  lv_obj_set_style_text_color(photo_info_label, lv_color_hex(0x7F8C8D),
                              0); 
  lv_obj_set_style_text_font(photo_info_label, UI_FONT_INFO, 0);
  lv_obj_set_style_pad_all(photo_info_label, 0, 0);
  lv_obj_set_style_translate_y(photo_info_label, 0, 0);
  lv_obj_add_flag(photo_info_label, LV_OBJ_FLAG_HIDDEN);

  /* upload_banner 鎳掑垱寤猴紙棣栨璋冪敤 ui_show_upload鏃跺垵濮嬪寲锛?*/
  upload_banner = NULL;

  
  loading_label = lv_label_create(scr);
  lv_label_set_text(loading_label, "LOADING.");
  lv_obj_set_style_text_color(loading_label, lv_color_hex(0x000000), 0);
  lv_obj_set_style_text_font(loading_label, &lv_font_montserrat_28, 0);
  lv_obj_align(loading_label, LV_ALIGN_CENTER, 0, 0); 

  loading_timer = lv_timer_create(loading_timer_cb, 500,
                                  NULL); 

  ESP_LOGI(TAG, "UI initialized 鈥?new layout %d脳%d", LCD_W, LCD_H);
  lv_va_init();
}

/* 鈹€鈹€ 鎵嬫満涓婁紶鐓х墖闈欐€佸彉閲忎笌鐘舵€佺淮鎶?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
static lv_obj_t *upload_overlay = NULL;
static lv_obj_t *upload_modal = NULL;
static lv_timer_t *upload_timer = NULL;
static uint8_t *new_photo_rgb565 = NULL;
static size_t new_photo_len = 0;
static char upload_message[256];
static char upload_uploader[64];

// 澹版槑澶栭儴鐨勬棫鍐呭瓨鍥炴敹鍑芥暟锛屽湪娓愬彉鍔ㄧ敾瀹屾垚鍚庤瑙﹀彂
extern void lvgl_ui_release_old_photo(void);

/* 鈹€鈹€ 鍔ㄧ敾鍏煎鎬у寘瑁呭嚱鏁?(閽堝楂樼骇缂栬瘧鍣ㄤ弗鑻涚被鍨嬪畨鍏ㄦ鏌? 鈹€鈹€ */
static void anim_set_x_cb(void *var, int32_t v) {
  lv_obj_set_x((lv_obj_t *)var, (lv_coord_t)v);
}

static void anim_set_y_cb(void *var, int32_t v) {
  lv_obj_set_y((lv_obj_t *)var, (lv_coord_t)v);
}

static void anim_set_opa_cb(void *var, int32_t v) {
  lv_obj_set_style_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void anim_set_bg_opa_cb(void *var, int32_t v) {
  lv_obj_set_style_bg_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

/* 鈹€鈹€ 绮掑瓙鐖嗙牬涓庡姩鏁堣緟鍔╁嚱鏁?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
static void delete_obj_anim_ready_cb(lv_anim_t *a) {
  if (a->var) {
    lv_obj_del(a->var);
  }
}

// 涓存椂椤跺眰鍥惧儚娣″嚭瀹屾垚鍚庣殑鍥炶皟锛岄攢姣佷复鏃跺浘鍍忓苟鍥炴敹鏃у浘鐗囩紦鍐插尯
static void old_img_fade_ready_cb(lv_anim_t *a) {
  if (a->var) {
    lv_obj_del(a->var);
  }
  img_temp = NULL; // 娓愬彉瀹屾垚锛岀疆绌轰复鏃惰鐩栧浘鍍忔寚閽?
  lvgl_ui_release_old_photo();
}

static void spawn_particles(lv_obj_t *parent) {
  static const lv_color_t colors[] = {
      {.full = 0xF86B}, // #FF6B6B
      {.full = 0xFED3}, // #FFD93D
      {.full = 0x6E57}, // #6BCB77
      {.full = 0x4CBF}, // #4D96FF
      {.full = 0xFBC2}  // #F78812
  };
  int color_count = sizeof(colors) / sizeof(colors[0]);

  for (int i = 0; i < 4; i++) {
    lv_obj_t *p = lv_obj_create(parent);
    lv_obj_set_size(p, 8, 8);
    lv_obj_set_style_radius(p, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(p, 0, 0);
    lv_obj_set_style_bg_color(p, colors[rand() % color_count], 0);

    // 灞呬腑璧峰浜庡睆骞曟涓績 (240, 400)
    lv_obj_align(p, LV_ALIGN_CENTER, 0, 0);

    int angle = rand() % 360;
    int dist = 100 + (rand() % 120); // 鏀惧皠璺濈 100 - 220 鍍忕礌

    // 鍒╃敤 LVGL 鍐呭缓楂樼簿搴︿笁瑙掑嚱鏁拌绠楁斁灏勫亸绉荤粓鐐?
    int32_t target_x = (lv_trigo_cos(angle) * dist) >> 15;
    int32_t target_y = (lv_trigo_sin(angle) * dist) >> 15;

    // X浣嶇Щ
    lv_anim_t ax;
    lv_anim_init(&ax);
    lv_anim_set_var(&ax, p);
    lv_anim_set_values(&ax, 0, target_x);
    lv_anim_set_time(&ax, 500);
    lv_anim_set_exec_cb(&ax,
                        anim_set_x_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
    lv_anim_start(&ax);

    // Y浣嶇Щ
    lv_anim_t ay;
    lv_anim_init(&ay);
    lv_anim_set_var(&ay, p);
    lv_anim_set_values(&ay, 0, target_y);
    lv_anim_set_time(&ay, 500);
    lv_anim_set_exec_cb(&ay,
                        anim_set_y_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
    lv_anim_start(&ay);

    // 閫忔槑搴︽笎闅愬苟鑷瘉
    lv_anim_t ao;
    lv_anim_init(&ao);
    lv_anim_set_var(&ao, p);
    lv_anim_set_values(&ao, 255, 0);
    lv_anim_set_time(&ao, 500);
    lv_anim_set_exec_cb(&ao, anim_set_opa_cb);
    lv_anim_set_ready_cb(&ao, delete_obj_anim_ready_cb);
    lv_anim_start(&ao);
  }
}

static void spawn_particles_timer_cb(lv_timer_t *t) {
  lv_obj_t *parent = (lv_obj_t *)t->user_data;
  if (parent) {
    spawn_particles(parent);
  }
  lv_timer_del(t);
}

static void fade_in_widget(lv_obj_t *obj, uint32_t delay) {
  lv_obj_set_style_opa(obj, 0, 0);
  lv_anim_t a;
  lv_anim_init(&a);
  lv_anim_set_var(&a, obj);
  lv_anim_set_values(&a, 0, 255);
  lv_anim_set_time(&a, 450);
  lv_anim_set_delay(&a, delay);
  lv_anim_set_exec_cb(&a,
                      anim_set_opa_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
  lv_anim_start(&a);
}


static void dismiss_overlay_ready_cb(lv_anim_t *a) {
  if (a->var) {
    lv_obj_del(a->var); // 閿€姣侀伄缃╁強寮圭獥瀛愮郴缁?
  }
  upload_overlay = NULL;
  upload_modal = NULL;
}

static void upload_dismiss_timer_cb(lv_timer_t *t) {
  (void)t;
  if (upload_timer == t) {
    upload_timer = NULL;
  }
  // 1. 閫€鍦哄姩鐢伙細閬僵涓庡脊绐楁贰鍑?500ms
  if (upload_overlay) {
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, upload_overlay);
    lv_anim_set_values(&a, 153, 0);
    lv_anim_set_time(&a, 500);
    lv_anim_set_exec_cb(
        &a, anim_set_bg_opa_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
    lv_anim_start(&a);
  }
  if (upload_modal) {
    lv_anim_t am;
    lv_anim_init(&am);
    lv_anim_set_var(&am, upload_modal);
    lv_anim_set_values(&am, 0, 600); /* 寰€涓嬫粦鍑哄睆骞曚箣澶栵紝瀹炵幇 Y 杞撮浂鍐呭瓨閫€鍦?*/
    lv_anim_set_time(&am, 500);
    lv_anim_set_exec_cb(&am,
                        anim_set_y_cb); /* 浣滅敤浜?Y 杞村潗鏍囷紝鍏嶉伃 OOM 濞佽儊 */
    lv_anim_set_ready_cb(&am, dismiss_overlay_ready_cb);
    lv_anim_start(&am);
  }

  // 2. 鐓х墖鏃犵紳璺ㄥ浘娓愬彉 (Crossfade)
  if (new_photo_rgb565 && img_main) {
    if (current_img_dsc.data != NULL) {
      // 鏈夋棫鍥剧墖鏃讹細鎵ц鐨勬笎闅?Crossfade
      static lv_img_dsc_t old_img_dsc;
      memcpy(&old_img_dsc, &current_img_dsc, sizeof(lv_img_dsc_t));

      // 鍒涘缓涓存椂椤跺眰瑕嗙洊鍥剧敤浜庤繃娓?      img_temp = lv_img_create(lv_scr_act());
      lv_obj_set_size(img_temp, current_img_dsc.header.w,
                      current_img_dsc.header.h);       /* 鍏ㄥ睆 */
      lv_obj_align(img_temp, LV_ALIGN_TOP_LEFT, 0, 0); /* 璧峰浜庨《瑙?(0, 0) */
      lv_img_set_src(img_temp, &old_img_dsc);
      lv_obj_move_foreground(img_temp);

      // 纭繚鐘舵€佹爮鍜屽簳閮ㄤ俊鎭爮浣嶄簬鏈€椤跺眰
      if (top_bar)
        lv_obj_move_foreground(top_bar);
      if (caption_container)
        lv_obj_move_foreground(caption_container);

      // 鍗囩骇搴曞眰鐪熷疄涓诲浘涓烘柊涓婁紶鐓х墖
      _set_photo_locked(new_photo_rgb565, upload_message, upload_uploader,
                        "浜戠涓婁紶");

      // 椤跺眰瑕嗙洊鍥惧儚鍦?500ms 鍐呮贰鍑猴紝骞惰嚜鍔ㄨЕ鍙戦攢姣佷笌鏃у唴瀛橀噴鏀?
      lv_anim_t ax;
      lv_anim_init(&ax);
      lv_anim_set_var(&ax, img_temp);
      lv_anim_set_values(&ax, 255, 0);
      lv_anim_set_time(&ax, 500);
      lv_anim_set_exec_cb(
          &ax, anim_set_opa_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
      lv_anim_set_ready_cb(&ax, old_img_fade_ready_cb);
      lv_anim_start(&ax);
    } else {
      // 鏈彂鐜版棫鍥撅紝鐩存帴鍔犺浇鏂扮収鐗?
      _set_photo_locked(new_photo_rgb565, upload_message, upload_uploader,
                        "鎵嬫満涓婁紶");
      // 姝ゆ椂鐩存帴瀹夊叏閲婃斁鑰佺収鐗囩紦鍐插尯
      lvgl_ui_release_old_photo();
    }
  }
}

bool ui_is_upload_animating(void) {
  return (upload_overlay != NULL || img_temp != NULL);
}

void ui_force_dismiss_upload(void) {
  // 1. 濡傛灉杩樺湪灞曠ず寮圭獥闃舵锛氱珛鍗抽攢姣佸脊绐椾笌閬僵锛屽畨鍏ㄥ洖鏀朵笂涓€寮犳棫鍥剧殑鍐呭瓨
  if (upload_overlay) {
    lv_obj_del(upload_overlay);
    upload_overlay = NULL;
    upload_modal = NULL;
    lvgl_ui_release_old_photo();
  }

  // 2. 濡傛灉宸茬粡杩涘叆 Crossfade 闃舵锛氱珛鍗冲己琛岄攢姣佹贰鍑虹殑涓存椂椤跺眰鍥撅紝瀹夊叏鍥炴敹鏃у浘
  if (img_temp) {
    lv_obj_del(img_temp);
    img_temp = NULL;
    lvgl_ui_release_old_photo();
  }

  // 3. 鍒犻櫎鏈埌鏈熺殑鍗曟娆ｈ祻瀹氭椂鍣?
  if (upload_timer) {
    lv_timer_del(upload_timer);
    upload_timer = NULL;
  }

  new_photo_rgb565 = NULL;
  new_photo_len = 0;
  upload_message[0] = '\0';
  upload_uploader[0] = '\0';
}

/* 鈹€鈹€ 鎵嬫満涓婁紶鎺ュ彛涓讳綋瀹炵幇 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€ */
void ui_show_upload(uint8_t *rgb565_data, size_t len, const char *message,
                    const char *uploader) {
  if (!rgb565_data || len != 768000) {
    ESP_LOGE(TAG, "ui_show_upload: Invalid parameters. data=%p, len=%d",
             rgb565_data, len);
    return;
  }

  // 缁堟涔嬪墠鐨勫姩鐢诲苟閲婃斁鍐呭瓨
  ui_force_dismiss_upload();

  // A. 缂撳瓨鏂扮収鐗囨暟鎹強鐣欒█淇℃伅
  new_photo_rgb565 = rgb565_data;
  new_photo_len = len;
  if (message) {
    strncpy(upload_message, message, sizeof(upload_message) - 1);
    upload_message[sizeof(upload_message) - 1] = '\0';
  } else {
    strcpy(upload_message, "闈欎韩缇庡ソ鏃跺厜~");
  }
  if (uploader) {
    strncpy(upload_uploader, uploader, sizeof(upload_uploader) - 1);
    upload_uploader[sizeof(upload_uploader) - 1] = '\0';
  } else {
    strcpy(upload_uploader, "unknown");
  }

  // B. Step 1: 鍒涘缓鍏ㄥ睆娌夋蹈寮忛粦鑹查檷鍣伄缃?
  upload_overlay = lv_obj_create(lv_scr_act());
  lv_obj_set_size(upload_overlay, lv_pct(100), lv_pct(100));
  lv_obj_align(upload_overlay, LV_ALIGN_CENTER, 0, 0);
  lv_obj_set_style_bg_color(upload_overlay, lv_color_hex(0x000000), 0);
  lv_obj_set_style_bg_opa(upload_overlay, 0, 0);
  lv_obj_set_style_border_width(upload_overlay, 0, 0);
  lv_obj_set_style_radius(upload_overlay, 0, 0);
  lv_obj_clear_flag(upload_overlay, LV_OBJ_FLAG_SCROLLABLE);

  // 榛戣壊鑳屾櫙娣″叆 400ms 鑷?60% 閫忔槑搴?(153)
  lv_anim_t a_overlay;
  lv_anim_init(&a_overlay);
  lv_anim_set_var(&a_overlay, upload_overlay);
  lv_anim_set_values(&a_overlay, 0, 153);
  lv_anim_set_time(&a_overlay, 400);
  lv_anim_set_exec_cb(
      &a_overlay,
      anim_set_bg_opa_cb); // 瀹屽叏瀵瑰簲绛惧悕锛屽交搴曞簾闄ゅ己鍒剁被鍨嬭浆鎹紒
  lv_anim_start(&a_overlay);

  // B. 鍒涘缓妯℃€佹寮圭獥
  upload_modal = lv_obj_create(upload_overlay);
  lv_obj_set_size(upload_modal, 360, 480);
  lv_obj_align(upload_modal, LV_ALIGN_CENTER, 0, 480);
  lv_obj_set_style_bg_color(upload_modal, lv_color_hex(0xFDF5E6), 0);
  lv_obj_set_style_radius(upload_modal, 16, 0);
  lv_obj_set_style_border_width(upload_modal, 0, 0);
  lv_obj_set_style_pad_all(upload_modal, 20, 0);
  lv_obj_clear_flag(upload_modal, LV_OBJ_FLAG_SCROLLABLE);

  // 缁樺埗杈规
  lv_obj_set_style_shadow_width(upload_modal, 0, 0);
  lv_obj_set_style_border_color(upload_modal, lv_color_hex(0xE67E22), 0);
  lv_obj_set_style_border_width(upload_modal, 2, 0);

  lv_obj_set_flex_flow(upload_modal, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(upload_modal, LV_FLEX_ALIGN_SPACE_BETWEEN,
                        LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

  // 婊戝叆鍔ㄧ敾
  lv_anim_t a_modal;
  lv_anim_init(&a_modal);
  lv_anim_set_var(&a_modal, upload_modal);
  lv_anim_set_values(&a_modal, 480, 0);
  lv_anim_set_time(&a_modal, 600);
  lv_anim_set_path_cb(&a_modal, lv_anim_path_overshoot);
  lv_anim_set_exec_cb(&a_modal, anim_set_y_cb);
  lv_anim_start(&a_modal);

  // D. 寮圭獥鍐呴儴浠舵帓鐗堜笌娣″叆璁捐
  // D1. 姗欒壊绮剧編鍦嗗湀淇＄鍥炬爣
  lv_obj_t *icon_circle = lv_obj_create(upload_modal);
  lv_obj_set_size(icon_circle, 80, 80);
  lv_obj_set_style_radius(icon_circle, LV_RADIUS_CIRCLE, 0);
  lv_obj_set_style_bg_color(icon_circle, lv_color_hex(0xE67E22),
                            0); // 鏆栨剰姗欒壊
  lv_obj_set_style_border_width(icon_circle, 0, 0);
  lv_obj_set_style_pad_all(icon_circle, 0, 0);
  lv_obj_clear_flag(icon_circle, LV_OBJ_FLAG_SCROLLABLE);

  lv_obj_t *icon_label = lv_label_create(icon_circle);
  lv_label_set_text(icon_label, "NEW");
  lv_obj_set_style_text_font(icon_label, UI_FONT_PRIMARY, 0);
  lv_obj_set_style_text_color(icon_label, lv_color_hex(0xFFFFFF), 0);
  lv_obj_center(icon_label);

  // D2. 涓婁紶鐓х墖鏍囬
  lv_obj_t *title_label = lv_label_create(upload_modal);
  lv_label_set_text(title_label, "鏀跺埌浜戠鎶曢€掞紒");
  lv_obj_set_style_text_font(title_label, UI_FONT_PRIMARY, 0);
  lv_obj_set_style_text_color(title_label, lv_color_hex(0x1A1A1A), 0);

  // D3. 鍙戦€佽€呯讲鍚?
  lv_obj_t *sender_label = lv_label_create(upload_modal);
  char s_buf[128];
  snprintf(s_buf, sizeof(s_buf), "鍙戜欢浜? %s", upload_uploader);
  lv_label_set_text(sender_label, s_buf);
  lv_obj_set_style_text_font(sender_label, UI_FONT_PRIMARY, 0);
  lv_obj_set_style_text_color(sender_label, lv_color_hex(0x7F8C8D), 0);

  // D4. 鍒嗗壊妯嚎
  lv_obj_t *div_line = lv_obj_create(upload_modal);
  lv_obj_set_size(div_line, 280, 2);
  lv_obj_set_style_bg_color(div_line, lv_color_hex(0xBDC3C7), 0);
  lv_obj_set_style_border_width(div_line, 0, 0);

  // D5. 绾稿紶淇′欢璐ㄦ劅鐣欒█鏂囨湰鍖?
  lv_obj_t *msg_container = lv_obj_create(upload_modal);
  lv_obj_set_size(msg_container, 320, 140);
  lv_obj_set_style_bg_color(msg_container, lv_color_hex(0xF5EBD6), 0);
  lv_obj_set_style_radius(msg_container, 8, 0);
  lv_obj_set_style_border_width(msg_container, 0, 0);
  lv_obj_set_style_pad_all(msg_container, 12, 0);
  lv_obj_clear_flag(msg_container, LV_OBJ_FLAG_SCROLLABLE);

  lv_obj_t *msg_label = lv_label_create(msg_container);
  lv_obj_set_width(msg_label, lv_pct(100));
  lv_label_set_long_mode(msg_label, LV_LABEL_LONG_WRAP);
  lv_label_set_text(msg_label, upload_message);
  lv_obj_set_style_text_font(msg_label, UI_FONT_PRIMARY, 0);
  lv_obj_set_style_text_color(msg_label, lv_color_hex(0x2C3E50), 0);
  lv_obj_align(msg_label, LV_ALIGN_TOP_LEFT, 0, 0);

  // E. Step 4: 娓愯繘寮忓欢杩熼樁姊贰鍏ワ紝璧嬩簣缁濅匠绌洪棿鑺傚鎰?
  fade_in_widget(icon_circle, 200);
  fade_in_widget(title_label, 200);
  fade_in_widget(sender_label, 350);
  fade_in_widget(msg_container, 500);

  // F. Step 3: 鍦?200ms 寮圭獥灞曞紑灏辩华鐨勭灛闂达紝鐖嗗彂褰╄壊鏀惧皠绮掑瓙
  lv_timer_create(spawn_particles_timer_cb, 200, upload_overlay);

  // G. Step 5: 鍚姩 3.8 绉掓璧忎笌娓愬彉閫€鍦哄崟娆″畾鏃跺櫒
  if (upload_timer) {
    lv_timer_del(upload_timer);
  }
  upload_timer = lv_timer_create(upload_dismiss_timer_cb, 3800, NULL);
  lv_timer_set_repeat_count(upload_timer, 1);
}

