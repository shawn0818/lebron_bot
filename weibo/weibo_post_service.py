import os
from typing import List, Optional, Dict, Any, Union
from weibo.weibo_picture_publisher import WeiboImagePublisher
from weibo.weibo_video_publisher import WeiboVideoPublisher
from utils.logger_handler import AppLogger


class WeiboPostService:
    """微博发布服务，提供统一的发布接口"""

    def __init__(self, cookie: Optional[str] = None):
        """初始化服务

        Args:
            cookie: 用户登录Cookie，如果不提供会尝试从环境变量获取
        """
        self.logger = AppLogger.get_logger(__name__, app_name='weibo')

        # 优先使用传入的cookie，否则尝试从环境变量获取
        self.cookie = cookie
        if not self.cookie:
            self.cookie = os.getenv("WB_COOKIES")
            if not self.cookie:
                self.logger.error("未设置WB_COOKIES环境变量且未提供cookie参数")
                raise ValueError("微博Cookie未提供且环境变量WB_COOKIES未设置")

        self.image_publisher = WeiboImagePublisher(self.cookie)
        self.video_publisher = WeiboVideoPublisher(self.cookie)

    def post_picture(self,
                     content: str,
                     image_paths: Union[str, List[str]]) -> Dict[str, Any]:
        """发布图片微博

        Args:
            content: 微博文本内容
            image_paths: 单个图片路径或图片路径列表

        Returns:
            Dict: 包含成功状态和消息的字典
        """
        try:
            self.logger.info(f"开始发布图片微博，图片数量: {len(image_paths) if isinstance(image_paths, list) else 1}")

            # 直接使用图片发布器的方法发布图片
            result = self.image_publisher.publish_images(image_paths, content)

            # 添加详细日志，记录完整的返回结果
            self.logger.debug(f"微博API返回结果: {result}")

            # WeiboImagePublisher中的publish_images方法已经处理了成功/失败的判断
            # 并返回了统一格式的结果，我们只需要信任这个结果
            if result and result.get("success") == True:
                self.logger.info(f"图片微博发布成功")
                return {"success": True, "message": result.get("message", "发布成功"), "data": result.get("data", {})}
            else:
                error_message = result.get("message", "未知错误")
                self.logger.error(f"图片微博发布失败: {error_message}")
                return {"success": False, "message": error_message, "data": result.get("data", {})}

        except Exception as e:
            error_message = f"发布图片微博失败: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            return {"success": False, "message": error_message}

    def post_video(self,
                   video_path: str,
                   title: str,
                   content: str,
                   cover_path: Optional[str] = None,
                   is_original: bool = True,
                   album_id: Optional[str] = None,
                   channel_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """发布视频微博"""
        try:
            self.logger.info(f"开始发布视频微博，视频路径: {video_path}")

            # 添加视频文件检查
            if not os.path.exists(video_path):
                error_message = f"视频文件不存在: {video_path}"
                self.logger.error(error_message)
                return {"success": False, "message": error_message}

            # 上传封面（如果有）
            cover_pid = None
            if cover_path:
                self.logger.info(f"上传视频封面: {cover_path}")
                try:
                    cover_info = self.image_publisher.upload_image(cover_path)
                    if cover_info:
                        cover_pid = cover_info.get('pid')
                        self.logger.info(f"封面上传成功，PID: {cover_pid}")
                except Exception as cover_error:
                    self.logger.warning(f"封面上传失败，将使用默认封面: {str(cover_error)}")

            # 使用进度回调函数
            def progress_callback(current, total, percentage):
                self.logger.info(f"视频上传进度: {current}/{total} 块 ({percentage:.2f}%)")

            # 上传并发布视频
            self.logger.info("开始上传视频...")
            upload_result = self.video_publisher.upload_video(
                file_path=video_path,
                progress_callback=progress_callback
            )

            if not upload_result or 'media_id' not in upload_result:
                error_message = "视频上传失败，未获取到media_id"
                self.logger.error(error_message)
                return {"success": False, "message": error_message}

            media_id = upload_result.get('media_id')
            self.logger.info(f"视频上传成功，media_id: {media_id}")

            # 发布视频
            self.logger.info("开始发布视频内容...")
            publish_result = self.video_publisher.publish_video(
                media_id=media_id,
                title=title,
                content=content,
                cover_pid=cover_pid,
                is_original=is_original,
                album_id=album_id,
                channel_ids=channel_ids
            )

            # 详细记录API返回
            self.logger.debug(f"发布视频API返回: {publish_result}")

            # 简化判断逻辑
            if publish_result.get('ok') == 1 or publish_result.get('code') == 100000 or "success" in str(
                    publish_result).lower():
                self.logger.info(f"视频微博发布成功!")
                return {"success": True, "message": "视频微博发布成功", "data": publish_result}
            else:
                message = publish_result.get('msg', '发布失败')
                self.logger.error(f"视频微博发布失败: {message}")
                return {"success": False, "message": message, "data": publish_result}

        except Exception as e:
            error_message = f"发布视频微博失败: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            return {"success": False, "message": error_message}

    def _check_file_exists(self, file_path, file_type="文件") -> bool:
        """检查文件是否存在，并记录日志

        Args:
            file_path: 文件路径
            file_type: 文件类型描述

        Returns:
            bool: 文件是否存在
        """
        import os.path

        if not file_path:
            self.logger.error(f"未指定{file_type}路径")
            return False

        if not os.path.exists(file_path):
            self.logger.error(f"{file_type}不存在: {file_path}")
            return False

        self.logger.info(f"找到{file_type}: {file_path}")
        return True


    def close(self):
        """清理资源"""
        if hasattr(self, 'image_publisher'):
            del self.image_publisher
        if hasattr(self, 'video_publisher'):
            del self.video_publisher

    def __enter__(self):
        """支持使用with语句"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出with语句块时自动关闭资源"""
        self.close()