import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from nba.fetcher.game_fetcher import GameFetcher
from utils.logger_handler import AppLogger


class BoxscoreSync:
    """
    比赛数据同步器
    负责从NBA API获取数据、转换并写入数据库
    """

    def __init__(self, db_manager, boxscore_repository=None, game_fetcher=None):
        """初始化比赛数据同步器"""
        self.db_manager = db_manager
        self.boxscore_repository = boxscore_repository  # 可选，用于查询
        self.game_fetcher = game_fetcher or GameFetcher()
        self.logger = AppLogger.get_logger(__name__, app_name='sqlite')

    def sync_boxscore(self, game_id: str, force_update: bool = False) -> Dict[str, Any]:
        """
        同步指定比赛的统计数据

        Args:
            game_id: 比赛ID
            force_update: 是否强制更新，默认为False

        Returns:
            Dict: 同步结果
        """
        start_time = datetime.now().isoformat()
        self.logger.info(f"开始同步比赛(ID:{game_id})的Boxscore数据...")

        try:
            # 获取boxscore数据
            boxscore_data = self.game_fetcher.get_boxscore_traditional(game_id, force_update)
            if not boxscore_data:
                raise ValueError(f"无法获取比赛(ID:{game_id})的Boxscore数据")

            # 解析和保存数据
            success_count, summary = self._save_boxscore_data(game_id, boxscore_data)

            end_time = datetime.now().isoformat()
            status = "success" if success_count > 0 else "failed"

            # 记录同步历史
            self._record_sync_history(game_id, status, start_time, end_time, success_count, summary)

            self.logger.info(f"比赛(ID:{game_id})Boxscore数据同步完成，状态: {status}")
            return {
                "status": status,
                "items_processed": 1,
                "items_succeeded": success_count,
                "summary": summary,
                "start_time": start_time,
                "end_time": end_time
            }

        except Exception as e:
            error_msg = f"同步比赛(ID:{game_id})Boxscore数据失败: {e}"
            self.logger.error(error_msg, exc_info=True)

            # 记录失败的同步历史
            self._record_sync_history(game_id, "failed", start_time, datetime.now().isoformat(), 0, {"error": str(e)})

            return {
                "status": "failed",
                "items_processed": 1,
                "items_succeeded": 0,
                "error": str(e)
            }

    def _record_sync_history(self, game_id: str, status: str, start_time: str, end_time: str,
                             items_processed: int, details: Dict) -> None:
        """记录同步历史到数据库"""
        try:
            cursor = self.db_manager.conn.cursor()
            cursor.execute('''
                INSERT INTO game_stats_sync_history 
                (sync_type, game_id, status, items_processed, items_succeeded, 
                start_time, end_time, details, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                'boxscore',
                game_id,
                status,
                items_processed,
                items_processed if status == "success" else 0,
                start_time,
                end_time,
                json.dumps(details),
                details.get("error", "") if status == "failed" else ""
            ))
            self.db_manager.conn.commit()
        except Exception as e:
            self.logger.error(f"记录同步历史失败: {e}")

    def _save_boxscore_data(self, game_id: str, boxscore_data: Dict) -> Tuple[int, Dict]:
        """
        解析并保存boxscore数据到数据库

        Args:
            game_id: 比赛ID
            boxscore_data: 从API获取的boxscore数据

        Returns:
            Tuple[int, Dict]: 成功保存的记录数和摘要信息
        """
        try:
            cursor = self.db_manager.conn.cursor()
            now = datetime.now().isoformat()
            success_count = 0
            summary = {
                "player_stats_count": 0,
                "home_team": "",
                "away_team": ""
            }

            # 1. 解析比赛基本信息
            game_info = self._extract_game_info(boxscore_data)
            if not game_info:
                raise ValueError(f"无法从Boxscore数据中提取比赛信息")

            # 添加到摘要
            summary["home_team"] = f"{game_info.get('home_team_city')} {game_info.get('home_team_name')}"
            summary["away_team"] = f"{game_info.get('away_team_city')} {game_info.get('away_team_name')}"

            # 2. 解析球员统计数据并与比赛信息合并
            player_stats = self._extract_player_stats(boxscore_data, game_id)
            if player_stats:
                for player_stat in player_stats:
                    # 合并比赛信息和球员统计数据
                    player_stat.update({
                        "game_id": game_id,
                        "home_team_id": game_info.get("home_team_id"),
                        "away_team_id": game_info.get("away_team_id"),
                        "home_team_tricode": game_info.get("home_team_tricode"),
                        "away_team_tricode": game_info.get("away_team_tricode"),
                        "home_team_name": game_info.get("home_team_name"),
                        "home_team_city": game_info.get("home_team_city"),
                        "away_team_name": game_info.get("away_team_name"),
                        "away_team_city": game_info.get("away_team_city"),
                        "game_status": game_info.get("game_status", 0),
                        "home_team_score": game_info.get("home_team_score", 0),
                        "away_team_score": game_info.get("away_team_score", 0),
                        "video_available": game_info.get("video_available", 0),
                        "last_updated_at": now
                    })

                    # 保存合并后的数据
                    self._save_or_update_player_boxscore(cursor, player_stat)
                    success_count += 1

                summary["player_stats_count"] = len(player_stats)

            self.db_manager.conn.commit()
            self.logger.info(f"成功保存比赛(ID:{game_id})的Boxscore数据，共{success_count}条记录")
            return success_count, summary

        except Exception as e:
            self.db_manager.conn.rollback()
            self.logger.error(f"保存Boxscore数据失败: {e}")
            raise

    def _extract_game_info(self, boxscore_data: Dict) -> Dict:
        """从boxscore数据中提取比赛基本信息"""
        try:
            # 初始化空字典
            game_info = {}

            # 访问boxScoreTraditional字段获取基本信息
            box_data = boxscore_data.get('boxScoreTraditional', {})
            if not box_data:
                return game_info

            # 提取基本信息
            game_id = box_data.get('gameId')
            home_team_id = box_data.get('homeTeamId')
            away_team_id = box_data.get('awayTeamId')

            # 获取主队信息
            home_team = box_data.get('homeTeam', {})
            home_team_name = home_team.get('teamName', '')
            home_team_city = home_team.get('teamCity', '')
            home_team_tricode = home_team.get('teamTricode', '')

            # 获取客队信息
            away_team = box_data.get('awayTeam', {})
            away_team_name = away_team.get('teamName', '')
            away_team_city = away_team.get('teamCity', '')
            away_team_tricode = away_team.get('teamTricode', '')

            # 获取主队和客队得分
            home_team_stats = home_team.get('statistics', {})
            away_team_stats = away_team.get('statistics', {})
            home_team_score = home_team_stats.get('points', 0)
            away_team_score = away_team_stats.get('points', 0)

            # 从meta字段中提取视频可用性
            meta = boxscore_data.get('meta', {})
            video_available = meta.get('videoAvailable', 0)

            # 根据得分情况确定比赛状态
            # 0: 未开始, 1: 进行中, 2: 已结束
            game_status = 0
            if home_team_score > 0 or away_team_score > 0:
                game_status = 2  # 假设有得分的比赛已结束

            # 构建比赛信息
            extracted_info = {
                "game_id": game_id,
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "home_team_name": home_team_name,
                "home_team_city": home_team_city,
                "home_team_tricode": home_team_tricode,
                "away_team_name": away_team_name,
                "away_team_city": away_team_city,
                "away_team_tricode": away_team_tricode,
                "home_team_score": home_team_score,
                "away_team_score": away_team_score,
                "game_status": game_status,
                "video_available": video_available
            }

            return extracted_info

        except Exception as e:
            self.logger.error(f"提取比赛信息失败: {e}")
            return {}

    def _extract_player_stats(self, boxscore_data: Dict, game_id: str) -> List[Dict]:
        """从boxscore数据中提取球员统计数据"""
        try:
            player_stats = []

            # 访问boxScoreTraditional字段获取球员统计
            box_data = boxscore_data.get('boxScoreTraditional', {})
            if not box_data:
                return player_stats

            # 处理主队球员数据
            home_team = box_data.get('homeTeam', {})
            home_team_id = box_data.get('homeTeamId')
            home_players = home_team.get('players', [])

            for player in home_players:
                stats = player.get('statistics', {})
                player_stat = {
                    "person_id": player.get('personId'),
                    "team_id": home_team_id,
                    # 球员个人信息字段
                    "first_name": player.get('firstName', ''),
                    "family_name": player.get('familyName', ''),
                    "name_i": player.get('nameI', ''),
                    "player_slug": player.get('playerSlug', ''),
                    "position": player.get('position', ''),
                    "jersey_num": player.get('jerseyNum', ''),
                    "comment": player.get('comment', ''),
                    "is_starter": 1 if player.get('position', '') else 0,
                    # 球员统计数据字段
                    "minutes": stats.get('minutes', ''),
                    "field_goals_made": stats.get('fieldGoalsMade', 0),
                    "field_goals_attempted": stats.get('fieldGoalsAttempted', 0),
                    "field_goals_percentage": stats.get('fieldGoalsPercentage', 0.0),
                    "three_pointers_made": stats.get('threePointersMade', 0),
                    "three_pointers_attempted": stats.get('threePointersAttempted', 0),
                    "three_pointers_percentage": stats.get('threePointersPercentage', 0.0),
                    "free_throws_made": stats.get('freeThrowsMade', 0),
                    "free_throws_attempted": stats.get('freeThrowsAttempted', 0),
                    "free_throws_percentage": stats.get('freeThrowsPercentage', 0.0),
                    "rebounds_offensive": stats.get('reboundsOffensive', 0),
                    "rebounds_defensive": stats.get('reboundsDefensive', 0),
                    "rebounds_total": stats.get('reboundsTotal', 0),
                    "assists": stats.get('assists', 0),
                    "steals": stats.get('steals', 0),
                    "blocks": stats.get('blocks', 0),
                    "turnovers": stats.get('turnovers', 0),
                    "fouls_personal": stats.get('foulsPersonal', 0),
                    "points": stats.get('points', 0),
                    "plus_minus_points": stats.get('plusMinusPoints', 0.0)
                }
                player_stats.append(player_stat)

            # 处理客队球员数据
            away_team = box_data.get('awayTeam', {})
            away_team_id = box_data.get('awayTeamId')
            away_players = away_team.get('players', [])

            for player in away_players:
                stats = player.get('statistics', {})
                player_stat = {
                    "person_id": player.get('personId'),
                    "team_id": away_team_id,
                    # 球员个人信息字段
                    "first_name": player.get('firstName', ''),
                    "family_name": player.get('familyName', ''),
                    "name_i": player.get('nameI', ''),
                    "player_slug": player.get('playerSlug', ''),
                    "position": player.get('position', ''),
                    "jersey_num": player.get('jerseyNum', ''),
                    "comment": player.get('comment', ''),
                    "is_starter": 1 if player.get('position', '') else 0,
                    # 球员统计数据字段
                    "minutes": stats.get('minutes', ''),
                    "field_goals_made": stats.get('fieldGoalsMade', 0),
                    "field_goals_attempted": stats.get('fieldGoalsAttempted', 0),
                    "field_goals_percentage": stats.get('fieldGoalsPercentage', 0.0),
                    "three_pointers_made": stats.get('threePointersMade', 0),
                    "three_pointers_attempted": stats.get('threePointersAttempted', 0),
                    "three_pointers_percentage": stats.get('threePointersPercentage', 0.0),
                    "free_throws_made": stats.get('freeThrowsMade', 0),
                    "free_throws_attempted": stats.get('freeThrowsAttempted', 0),
                    "free_throws_percentage": stats.get('freeThrowsPercentage', 0.0),
                    "rebounds_offensive": stats.get('reboundsOffensive', 0),
                    "rebounds_defensive": stats.get('reboundsDefensive', 0),
                    "rebounds_total": stats.get('reboundsTotal', 0),
                    "assists": stats.get('assists', 0),
                    "steals": stats.get('steals', 0),
                    "blocks": stats.get('blocks', 0),
                    "turnovers": stats.get('turnovers', 0),
                    "fouls_personal": stats.get('foulsPersonal', 0),
                    "points": stats.get('points', 0),
                    "plus_minus_points": stats.get('plusMinusPoints', 0.0)
                }
                player_stats.append(player_stat)

            return player_stats

        except Exception as e:
            self.logger.error(f"提取球员统计数据失败: {e}")
            return []

    def _save_or_update_player_boxscore(self, cursor, player_stat: Dict) -> None:
        """保存或更新球员比赛统计数据"""
        try:
            game_id = player_stat.get('game_id')
            person_id = player_stat.get('person_id')

            # 检查是否已存在
            cursor.execute("SELECT game_id FROM statistics WHERE game_id = ? AND person_id = ?",
                           (game_id, person_id))
            exists = cursor.fetchone()

            if exists:
                # 更新现有记录
                placeholders = ", ".join([f"{k} = ?" for k in player_stat.keys()
                                          if k not in ('game_id', 'person_id')])
                values = [v for k, v in player_stat.items() if k not in ('game_id', 'person_id')]
                values.append(game_id)  # WHERE条件的值
                values.append(person_id)  # WHERE条件的值

                cursor.execute(f"UPDATE statistics SET {placeholders} WHERE game_id = ? AND person_id = ?",
                               values)
            else:
                # 插入新记录
                placeholders = ", ".join(["?"] * len(player_stat))
                columns = ", ".join(player_stat.keys())
                values = list(player_stat.values())

                cursor.execute(f"INSERT INTO statistics ({columns}) VALUES ({placeholders})", values)

        except Exception as e:
            self.logger.error(f"保存或更新球员比赛统计数据失败: {e}")
            raise