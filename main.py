import asyncio
from twscrape import API, gather
import os
import json
from datetime import datetime as dt
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from typing import List, Dict, Optional
import backoff


# Pydantic models for structured output
class BioKeyword(BaseModel):
	keyword: str = Field(..., description="A meaningful noun or phrase from the user's bio")
	evidence: str = Field(..., description="Brief evidence supporting the keyword")

class PersonalityTrait(BaseModel):
	trait: str = Field(..., description="A personality trait (e.g., curious, witty)")
	evidence: str = Field(..., description="Evidence from posts/replies, including specific reply examples")

class TopPost(BaseModel):
	rawContent: str = Field(..., description="The raw content of the post")
	likes: int = Field(..., ge=0, description="Number of likes")
	retweets: int = Field(..., ge=0, description="Number of retweets")
	views: int = Field(..., ge=0, description="Number of views, 0 if missing")
	date: str = Field(..., description="ISO format date of the post")

class Avatar(BaseModel):
	username: str = Field(..., description="The user's username")
	demographics: Dict[str, str | List[BioKeyword] | int] = Field(..., description="Contains location, bio_keywords, joined_year")
	personality: Dict[str, List[PersonalityTrait] | str] = Field(..., description="Contains traits, content_style, interaction_style")
	interests: List[str] = Field(..., description="List of relevant topics/keywords from posts/replies")
	content_summary: Dict[str, str | List[str]] = Field(..., description="Contains posts, replies, hashtags, mentions")
	engagement: Dict[str, float | List[TopPost]] = Field(..., description="Contains avg_likes, avg_retweets, avg_views, top_posts")
	activity: Dict[str, int | str] = Field(..., description="Contains post_count, reply_count, total_statuses, time_range")


def get_cookies():
	os.makedirs('input', exist_ok=True)
	with open('input/cookies.txt', 'r', encoding='utf-8') as f:
		cookies = f.read().strip()
	return cookies


async def get_followers(api, user_id, cache_file):
	if os.path.exists(cache_file):
		print("Using cached followers")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Fetching followers")
		followers = await gather(api.followers(user_id))
		follower_dicts = [follower.dict() for follower in followers]
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(follower_dicts, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return follower_dicts


async def get_following(api, user_id, cache_file):
	if os.path.exists(cache_file):
		print("Using cached following")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Fetching following")
		following = await gather(api.following(user_id))
		following_dicts = [following.dict() for following in following]
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(following_dicts, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return following_dicts


async def get_follow_all(followers, following, target_user, cache_file, include_self):
	if os.path.exists(cache_file):
		print("Using cached follow-all")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print("Creating follow-all union")
		users = followers + following
		if include_self:
			print(f"Including target user: @{target_user.username}")
			users.append(target_user.dict())
		follow_all_dict = {user['id']: user for user in users}
		follow_all = list(follow_all_dict.values())
		with open(cache_file, 'w', encoding='utf-8') as f:
			# noinspection PyTypeChecker
			json.dump(follow_all, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
		return follow_all


async def get_user_data(api, user_dict, cache_dir, fetch_limit, replies_limit):
	os.makedirs(cache_dir, exist_ok=True)
	user_id = user_dict['id']
	cache_file = f"{cache_dir}/{user_id}-data.json"
	if os.path.exists(cache_file):
		print(f"Using cached data for @{user_dict['username']}")
		with open(cache_file, 'r', encoding='utf-8') as f:
			return json.load(f)
	else:
		print(f"Fetching tweets for @{user_dict['username']}")
		try:
			tweets = await gather(api.user_tweets(user_id, limit=fetch_limit))
			tweets_dicts = [tweet.dict() for tweet in tweets]
			total_replies = 0
			for t in tweets_dicts:
				t.pop('media', None)
				t.pop('user', None)
				if replies_limit == -1 and t.get('replyCount', 0) > 0:
					t['replies'] = [reply.dict() for reply in await gather(api.tweet_replies(t['id']))]
				elif replies_limit > 0 and t.get('replyCount', 0) > 0:
					t['replies'] = [reply.dict() for reply in await gather(api.tweet_replies(t['id'], limit=replies_limit))]
				else:
					t['replies'] = []
				for r in t.get('replies', []):
					r.pop('media', None)
					r.pop('user', None)
				total_replies += len(t['replies'])
			user_data = {
				'profile': user_dict,
				'tweets': tweets_dicts
			}
			with open(cache_file, 'w', encoding='utf-8') as f:
				# noinspection PyTypeChecker
				json.dump(user_data, f, indent=4, default=lambda o: o.isoformat() if isinstance(o, dt) else o)
			print(f"Fetched {len(tweets_dicts)} tweets, {total_replies} replies for @{user_dict['username']}")
			await asyncio.sleep(2)
			return user_data
		except Exception as e:
			print(f"Error fetching data for @{user_dict['username']}: {str(e)}")
			return None


async def aggregate_to_markdown(cache_dir, min_likes_posts, min_likes_replies):
	total_posts = 0
	total_replies = 0
	processed_users = 0
	skipped_users = 0
	print("Regenerating markdown files...")
	for filename in os.listdir(cache_dir):
		if filename.endswith('.json'):
			json_path = os.path.join(cache_dir, filename)
			try:
				with open(json_path, 'r', encoding='utf-8') as f:
					data = json.load(f)
				if 'profile' not in data:
					print(f"Skipping invalid JSON file {filename}: missing 'profile' key")
					skipped_users += 1
					continue
				profile = data['profile']
				tweets = sorted(data['tweets'], key=lambda t: dt.fromisoformat(t['date']), reverse=True)  # Newest first
				processed_users += 1

				md_content = f"# Profile: @{profile['username']}\n"
				md_content += f"Bio: {profile['rawDescription']}\n"
				md_content += f"Location: {profile['location']}\n"
				md_content += f"Joined: {profile['created']}\n"
				md_content += f"Followers: {profile['followersCount']}\n"
				md_content += f"Following: {profile['friendsCount']}\n"
				md_content += f"Tweets: {profile['statusesCount']}\n"
				md_content += f"Likes: {profile['favouritesCount']}\n"
				md_content += f"Verified: {profile['verified']}\n"
				md_content += f"Blue: {profile['blue']}\n\n"

				included_posts = 0
				included_replies = 0
				for tweet in tweets:
					if tweet['likeCount'] >= min_likes_posts:
						included_posts += 1
						md_content += f"# Post by @{profile['username']} at {tweet['date']} (likes: {tweet['likeCount']}, retweets: {tweet['retweetCount']}, replies: {tweet['replyCount']}, views: {tweet['viewCount']})\n"
						md_content += f"{tweet['rawContent']}\n"
						if tweet['hashtags']:
							md_content += f"Hashtags: {', '.join(tweet['hashtags'])}\n"
						if tweet['mentionedUsers']:
							md_content += f"Mentions: {', '.join('@' + u['username'] for u in tweet['mentionedUsers'])}\n"
						if tweet['links']:
							md_content += f"Links: {', '.join(l['text'] for l in tweet['links'])}\n"
						if tweet.get('possibly_sensitive', False):
							md_content += "(Possibly sensitive)\n"
						md_content += "\n"
						for reply in tweet['replies']:
							if reply['likeCount'] >= min_likes_replies:
								included_replies += 1
								md_content += f"## Reply at {reply['date']} (likes: {reply['likeCount']}, retweets: {reply['retweetCount']}, views: {reply['viewCount']})\n"
								md_content += f"{reply['rawContent']}\n"
								if reply.get('possibly_sensitive', False):
									md_content += "(Possibly sensitive)\n"
								md_content += "\n"

				md_filename = filename.replace('.json', '.md')
				md_path = os.path.join(cache_dir, md_filename)
				with open(md_path, 'w', encoding='utf-8') as f:
					f.write(md_content)

				print(f"Aggregated @{profile['username']}: Included {included_posts} posts, {included_replies} replies")
				total_posts += included_posts
				total_replies += included_replies
			except Exception as e:
				print(f"Skipping invalid JSON file {filename}: {str(e)}")
				skipped_users += 1

	print(f"Aggregation complete: Processed {processed_users} users, {skipped_users} skipped, included {total_posts} posts, {total_replies} replies with min_likes_posts={min_likes_posts}, min_likes_replies={min_likes_replies}")


async def generate_avatars(cache_dir: str, avatar_dir: str, max_concurrent: int = 5) -> None:
	os.makedirs(avatar_dir, exist_ok=True)
	client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
	semaphore = asyncio.Semaphore(max_concurrent)  # Limit concurrent requests
	processed_users = 0
	failed_users = 0
	tasks = []

	print("Generating avatars...")

	# Backoff decorator for retrying on rate limit errors
	@backoff.on_exception(
		backoff.expo,
		exception=(Exception,),  # Replace with specific OpenAI rate limit exception if available
		max_tries=5,
		max_time=300
	)
	async def process_user(_filename: str, _semaphore: asyncio.Semaphore) -> Optional[tuple[str, Dict]]:
		nonlocal processed_users, failed_users
		if not _filename.endswith('.md'):
			return None

		_user_id = _filename.split('-')[0]
		avatar_path = os.path.join(avatar_dir, f"{_user_id}-avatar.json")
		if os.path.exists(avatar_path):
			print(f"Using cached avatar for user_id {_user_id}")
			with open(avatar_path, 'r', encoding='utf-8') as f:
				json.load(f)  # Verify file
			processed_users += 1
			return None

		async with _semaphore:
			processed_users += 1
			with open(os.path.join(cache_dir, _filename), 'r', encoding='utf-8') as f:
				md_content = f.read()

			try:
				print(f"Starting avatar generation for user_id {_user_id}")
				response = await client.chat.completions.create(
					model="gpt-4.1-2025-04-14",
					messages=[
						{
							"role": "system",
							"content": """
You are given a markdown file with a user's profile, posts, and replies. Create an audience avatar by analyzing the content objectively, emphasizing personality traits and content style, with heavy weight on replies due to their high volume. Output JSON conforming to the following schema:

{
	"username": str,
	"demographics": {
		"location": str,
		"bio_keywords": [{"keyword": str, "evidence": str}],
		"joined_year": int
	},
	"personality": {
		"traits": [{"trait": str, "evidence": str}],
		"content_style": str,
		"interaction_style": str
	},
	"interests": [str],
	"content_summary": {
		"posts": str,
		"replies": str,
		"hashtags": [str],
		"mentions": [str]
	},
	"engagement": {
		"avg_likes": float,
		"avg_retweets": float,
		"avg_views": float,
		"top_posts": [{"rawContent": str, "likes": int, "retweets": int, "views": int, "date": str}]
	},
	"activity": {
		"post_count": int,
		"reply_count": int,
		"total_statuses": int,
		"time_range": str
	}
}

Rules:
- For "engagement.top_posts", ensure "likes", "retweets", and "views" are integers. If "views" is unavailable in the input data, set it to 0.
- Ensure all numeric fields are valid numbers and not null.
- Do not include null values in "top_posts" fields unless explicitly allowed.

Input:
{markdown_content}
Output JSON only, strictly adhering to the schema.
"""
						},
						{"role": "user", "content": md_content}
					],
					response_format={"type": "json_object"}
				)
				print(f"Received response from GPT-4.1 for user_id {_user_id}")
				raw_avatar = json.loads(response.choices[0].message.content)

				# Normalize top_posts to ensure valid integers
				if 'engagement' in raw_avatar and 'top_posts' in raw_avatar['engagement']:
					for post in raw_avatar['engagement']['top_posts']:
						post['likes'] = int(post.get('likes', 0))
						post['retweets'] = int(post.get('retweets', 0))
						post['views'] = int(post.get('views', 0))  # Default to 0 if None or missing

				# Validate with Pydantic
				_avatar = Avatar(**raw_avatar)

				with open(avatar_path, 'w', encoding='utf-8') as f:
					# noinspection PyTypeChecker
					json.dump(_avatar.model_dump(), f, indent=4)
				print(f"Generated avatar for {_avatar.username}")
				return (_user_id, _avatar.model_dump())
			except ValidationError as ve:
				print(f"Validation error for user_id {_user_id}: {str(ve)}")
				print(f"Raw API response: {raw_avatar}")
				failed_users += 1
				return None
			except Exception as e:
				print(f"Failed to generate avatar for user_id {_user_id}: {str(e)}")
				print(f"Raw API response: {raw_avatar if 'raw_avatar' in locals() else 'Not received'}")
				failed_users += 1
				raise  # Re-raise for backoff to handle retries

	# Collect tasks for all users
	for filename in os.listdir(cache_dir):
		tasks.append(process_user(filename, semaphore))

	# Run tasks concurrently
	results = await asyncio.gather(*tasks, return_exceptions=True)

	# Process results (optional, for logging or further handling)
	for result in results:
		if isinstance(result, Exception):
			print(f"Task failed with exception: {str(result)}")
			failed_users += 1
		elif result is not None:
			user_id, avatar = result
			print(f"Completed processing for user_id {user_id}: @{avatar['username']}")

	print(f"Avatar generation complete: Processed {processed_users} users, {failed_users} failed")


async def aggregate_avatars(avatar_dir, target_username):
	output_file = f"output/{target_username}-avatars.json"
	print("Regenerating aggregated avatars...")
	avatars = []
	processed_avatars = 0
	failed_avatars = 0
	for filename in os.listdir(avatar_dir):
		if filename.endswith('-avatar.json'):
			avatar_path = os.path.join(avatar_dir, filename)
			try:
				with open(avatar_path, 'r', encoding='utf-8') as f:
					avatar = json.load(f)
				avatars.append(avatar)
				print(f"Aggregated avatar for {avatar['username']}")
				processed_avatars += 1
			except Exception as e:
				print(f"Failed to aggregate avatar for {filename}: {str(e)}")
				failed_avatars += 1
	with open(output_file, 'w', encoding='utf-8') as f:
		# noinspection PyTypeChecker
		json.dump(avatars, f, indent=4)
	print(f"Aggregation complete: Processed {processed_avatars} avatars, {failed_avatars} failed, saved to {output_file}")
	return avatars


async def aggregate_avatars_lite(avatars: List[Dict], target_username: str) -> None:
	output_file = f"output/{target_username}-avatars-lite.md"
	print(f"Starting generation of lite avatars markdown to {output_file}")
	try:
		with open(output_file, 'w', encoding='utf-8') as f:
			print(f"Opened output file: {output_file}")
			if not isinstance(avatars, list):
				print(f"Error: Input 'avatars' is not a list, got type {type(avatars)}: {avatars}")
				raise ValueError("Input 'avatars' must be a list")
			print(f"Processing {len(avatars)} avatars")

			for idx, avatar in enumerate(avatars, 1):
				print(f"Processing avatar {idx}/{len(avatars)}")
				if not isinstance(avatar, dict):
					print(f"Error: Avatar {idx} is not a dictionary, got type {type(avatar)}: {avatar}")
					raise ValueError(f"Avatar {idx} is not a dictionary")

				username = avatar.get('username', 'Unknown')
				print(f"Username: @{username}")
				f.write(f"# @{username}\n\n")

				# Bio Keywords
				demographics = avatar.get('demographics', {})
				bio_keywords = demographics.get('bio_keywords', [])
				print(f"Found {len(bio_keywords)} bio keywords")
				f.write("## Bio Keywords\n\n")
				for kw in bio_keywords:
					keyword = kw.get('keyword', '')
					evidence = kw.get('evidence', '')
					if not keyword:
						print(f"Error: Missing 'keyword' in bio keyword: {kw}")
						raise ValueError(f"Missing 'keyword' in bio keyword for @{username}: {kw}")
					print(f"Writing bio keyword: {keyword}")
					f.write(f"- {keyword}: {evidence}\n")
				f.write("\n")

				# Personality Traits
				personality = avatar.get('personality', {})
				traits = personality.get('traits', [])
				print(f"Found {len(traits)} personality traits")
				f.write("## Personality Traits\n\n")
				for trait in traits:
					trait_name = trait.get('trait', '')
					evidence = trait.get('evidence', '')
					if not trait_name:
						print(f"Error: Missing 'trait' in trait: {trait}")
						raise ValueError(f"Missing 'trait' in trait for @{username}: {trait}")
					print(f"Writing trait: {trait_name}")
					f.write(f"- {trait_name}: {evidence}\n")
				f.write("\n")

				# Interests
				interests = avatar.get('interests', [])
				print(f"Found {len(interests)} interests")
				f.write("## Interests\n\n")
				for interest in interests:
					print(f"Writing interest: {interest}")
					f.write(f"- {interest}\n")
				f.write("\n")

				# Posts Summary
				content_summary = avatar.get('content_summary', {})
				posts_summary = content_summary.get('posts', '')
				print(f"Writing posts summary: {posts_summary}")
				f.write("## Posts Summary\n\n")
				f.write(f"{posts_summary}\n\n")

				# Replies Summary
				replies_summary = content_summary.get('replies', '')
				print(f"Writing replies summary: {replies_summary}")
				f.write("## Replies Summary\n\n")
				f.write(f"{replies_summary}\n\n")

				# Hashtags
				hashtags = content_summary.get('hashtags', [])
				print(f"Found {len(hashtags)} hashtags")
				f.write("## Hashtags\n\n")
				for ht in hashtags:
					print(f"Writing hashtag: #{ht}")
					f.write(f"- #{ht}\n")
				f.write("\n")

		print(f"Lite avatars markdown generation complete: saved to {output_file}")
	except Exception as e:
		print(f"Failed to generate lite avatars markdown: {str(e)}")
		raise


async def main(target_username, include_self):
	os.makedirs('input', exist_ok=True)
	os.makedirs('output', exist_ok=True)
	os.makedirs('output/users', exist_ok=True)
	os.makedirs('output/avatars', exist_ok=True)

	api = API(pool='input/accounts.db')

	await api.pool.add_account("unused", "unused", "unused@example.com", "unused", cookies=get_cookies())
	await api.pool.login_all()

	print(f"Target user: @{target_username}")
	user = await api.user_by_login(target_username)
	print(f"User ID: {user.id}")

	cache_file_followers = f"output/{target_username}-followers.json"
	followers = await get_followers(api, user.id, cache_file_followers)
	print(f"Got {len(followers)} followers")

	cache_file_following = f"output/{target_username}-following.json"
	following = await get_following(api, user.id, cache_file_following)
	print(f"Got {len(following)} following")

	cache_file_follow_all = f"output/{target_username}-follow-all.json"
	follow_all = await get_follow_all(followers, following, user, cache_file_follow_all, include_self)
	print(f"Got {len(follow_all)} unique users in follow-all")

	print("Processing user data...")
	user_datas = []
	total_tweets = 0
	total_replies = 0
	skipped_users = 0
	for idx, user in enumerate(follow_all, 1):
		user_data = await get_user_data(api, user, cache_dir='output/users', fetch_limit=20, replies_limit=5)
		if user_data:
			user_datas.append(user_data)
			total_tweets += len(user_data['tweets'])
			total_replies += sum(len(t['replies']) for t in user_data['tweets'])
		else:
			skipped_users += 1
		print(f"Processed {idx}/{len(follow_all)} users")
	print(f"Summary: Processed {len(user_datas)} users, {skipped_users} skipped, {total_tweets} tweets, {total_replies} replies")

	await aggregate_to_markdown('output/users', min_likes_posts=2, min_likes_replies=2)
	await generate_avatars('output/users', 'output/avatars')
	avatars = await aggregate_avatars('output/avatars', target_username)
	await aggregate_avatars_lite(avatars, target_username)


if __name__ == "__main__":
	asyncio.run(main(target_username="drxwilhelm", include_self=True))
