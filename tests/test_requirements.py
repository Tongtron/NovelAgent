import unittest

from novel_agent.models import NovelCreateRequest
from novel_agent.requirements import RequirementConflict, validate_requirements
from novel_agent.tags import public_tag_catalog


class RequirementTests(unittest.TestCase):
    def test_conflicting_romance_tags_are_rejected(self) -> None:
        request = NovelCreateRequest(
            genre="末世",
            romance="无感情线",
            elements=["先婚后爱"],
            idea="两个目标完全相反的标签。",
        )
        with self.assertRaises(RequirementConflict):
            validate_requirements(request)

    def test_must_have_cannot_also_be_excluded(self) -> None:
        request = NovelCreateRequest(
            genre="悬疑",
            idea="旧城调查。",
            must_have=["群像"],
            exclude=["群像"],
        )
        with self.assertRaises(RequirementConflict):
            validate_requirements(request)

    def test_channel_catalog_has_distinct_male_and_female_options(self) -> None:
        catalog = public_tag_catalog()["channels"]
        self.assertIn("都市高武", catalog["男频"]["genres"])
        self.assertIn("现言脑洞", catalog["女频"]["genres"])
        self.assertNotEqual(
            catalog["男频"]["protagonist_tags"],
            catalog["女频"]["protagonist_tags"],
        )

    def test_female_channel_accepts_female_genre_and_rejects_male_only_genre(self) -> None:
        valid = NovelCreateRequest(
            audience_channel="女频",
            genre="现言脑洞",
            experiences=["甜宠"],
            elements=["穿书"],
            protagonist_tags=["大女主"],
            idea="女主进入自己参与制作的恋爱游戏。",
        )
        validate_requirements(valid)
        invalid = valid.model_copy(update={"genre": "都市高武"})
        with self.assertRaises(RequirementConflict):
            validate_requirements(invalid)


if __name__ == "__main__":
    unittest.main()
